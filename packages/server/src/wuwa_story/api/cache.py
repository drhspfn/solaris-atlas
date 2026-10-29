"""Bounded Redis response cache for explicitly public, read-only game APIs."""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from wuwa_story.config.settings import Settings
from wuwa_story.db.session import engine

logger = logging.getLogger(__name__)
PUBLIC_ROOTS = {
    "catalog", "categories", "items", "locations", "characters", "quests",
    "dialogue", "story-map", "maps", "nodes", "graph", "search", "releases", "story-analysis", "events", "notices", "cutscenes",
}
REVISION_SQL = text("""
    SELECT md5(string_agg(table_name || ':' || transaction_id::text, ',' ORDER BY table_name))
    FROM ops.public_cache_revision
""")

# The story-map handler reads raw snapshots, release/import state, localized
# titles and quest revisions; public_addresses also reads quest node slugs.
# Keep this list in sync when either response projection gains a dependency.
STORY_MAP_TABLES = (
    "core.quest", "graph.node", "graph.node_revision", "i18n.locale",
    "i18n.localization_content", "i18n.localization_key", "i18n.localization_value",
    "ops.game_release", "ops.import_run", "raw.source_file", "raw.source_record",
)
STORY_MAP_REVISION_SQL = text("""
    SELECT CASE WHEN count(*) = :expected_count AND count(t.oid) = :expected_count
           THEN md5(string_agg(r.table_name || ':' || r.transaction_id::text, ','
                               ORDER BY r.table_name)) END
    FROM ops.public_cache_revision r
    LEFT JOIN pg_namespace n ON n.nspname = split_part(r.table_name, '.', 1)
    LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = split_part(r.table_name, '.', 2)
    LEFT JOIN pg_trigger t ON t.tgrelid = c.oid AND t.tgname = 'public_cache_changed'
        AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A') AND t.tgtype = 60
        AND t.tgfoid = to_regprocedure('ops.bump_public_cache_revision()')
    WHERE r.table_name = ANY(:tables)
""")

_REVISION_CACHE = {"value": "", "expires": 0.0}
_REVISION_LOCK = asyncio.Lock()
_STORY_MAP_REVISION_CACHE = {"value": "", "expires": 0.0}
_STORY_MAP_REVISION_LOCK = asyncio.Lock()


async def content_revision() -> str:
    # One tiny metadata read on a hit, never the multimillion-row content tables.
    # Reading from PostgreSQL makes invalidation transactional, even if Redis
    # was unavailable when a worker committed or a writer crashes after COMMIT.
    now = time.monotonic()
    if _REVISION_CACHE["value"] and now < _REVISION_CACHE["expires"]:
        return _REVISION_CACHE["value"]

    # A cold revision must not create a DB connection for every concurrent cache hit.
    async with asyncio.timeout(1), _REVISION_LOCK:
        now = time.monotonic()
        if _REVISION_CACHE["value"] and now < _REVISION_CACHE["expires"]:
            return _REVISION_CACHE["value"]
        async with engine.connect() as connection:
            revision = await connection.scalar(REVISION_SQL)
        if not revision:
            raise RuntimeError("Public cache revision migration has not been installed")
        _REVISION_CACHE["value"] = str(revision)
        # Completed media imports must invalidate cached empty audio links promptly.
        _REVISION_CACHE["expires"] = time.monotonic() + 5.0
        return _REVISION_CACHE["value"]


async def story_map_revision() -> str:
    now = time.monotonic()
    if _STORY_MAP_REVISION_CACHE["value"] and now < _STORY_MAP_REVISION_CACHE["expires"]:
        return _STORY_MAP_REVISION_CACHE["value"]
    async with asyncio.timeout(1), _STORY_MAP_REVISION_LOCK:
        now = time.monotonic()
        if _STORY_MAP_REVISION_CACHE["value"] and now < _STORY_MAP_REVISION_CACHE["expires"]:
            return _STORY_MAP_REVISION_CACHE["value"]
        async with engine.connect() as connection:
            revision = await connection.scalar(STORY_MAP_REVISION_SQL, {
                "expected_count": len(STORY_MAP_TABLES), "tables": list(STORY_MAP_TABLES),
            })
        if not revision:
            # Never cache a map when a dependency lacks its revision or trigger.
            raise RuntimeError("Story-map cache revision coverage is incomplete")
        _STORY_MAP_REVISION_CACHE["value"] = str(revision)
        _STORY_MAP_REVISION_CACHE["expires"] = time.monotonic() + 5.0
        return _STORY_MAP_REVISION_CACHE["value"]


def cache_key(scope: Scope, revision: str, namespace: str) -> str:
    # Sort keys only: preserve duplicate value order because handlers can use
    # the last occurrence. Never put query text or credentials in Redis keys/logs.
    query = sorted(parse_qsl(scope.get("query_string", b"").decode("utf-8"),
                             keep_blank_values=True), key=lambda pair: pair[0])
    payload = [scope["path"], query, Headers(
        scope=scope).get("accept-language", "")]
    digest = hashlib.sha256(json.dumps(
        payload, ensure_ascii=True).encode()).hexdigest()
    return f"{namespace}:{revision}:{digest}"


def public_request(scope: Scope) -> bool:
    headers = Headers(scope=scope)
    return (
        scope["method"] == "GET"
        and scope["path"].strip("/").split("/", 1)[0] in PUBLIC_ROOTS
        and not headers.get("authorization")
        and not headers.get("cookie")
        and not headers.get("range")
        and len(scope.get("query_string", b"")) <= 8192
    )


class PublicResponseCache:
    def __init__(
        self, app: ASGIApp, settings: Settings, redis: Redis | None,
        revision_reader: Callable[[], Awaitable[str]] = content_revision,
        story_map_revision_reader: Callable[[], Awaitable[str]] = story_map_revision,
    ) -> None:
        self.app = app
        self.redis = redis
        self.settings = settings
        self.revision_reader = revision_reader
        self.story_map_revision_reader = story_map_revision_reader
        # Configuration changes must not serve links to a previous media host.
        config = [settings.api_cache_namespace, settings.media_public_base_url,
                  settings.s3_endpoint_url, settings.s3_bucket,
                  settings.database_url, settings.app_env]
        fingerprint = hashlib.sha256(json.dumps(config).encode())
        # A deployment changing response shape cannot reuse the preceding build's cache.
        source_root = Path(__file__).resolve().parents[1]
        for source in sorted(source_root.rglob("*.py")):
            fingerprint.update(str(source.relative_to(
                source_root)).replace("\\", "/").encode())
            fingerprint.update(source.read_bytes())
        self.namespace = fingerprint.hexdigest()[:24]
        self.last_warning = 0.0

    def warn(self) -> None:
        now = time.monotonic()
        if now - self.last_warning >= 60:
            logger.warning("api.cache_unavailable; serving origin responses")
            self.last_warning = now

    async def command(self, operation: Awaitable[Any]) -> Any:
        # Bound the entire operation, including any redis-py retry delay.
        async with asyncio.timeout(self.settings.api_cache_timeout_seconds):
            return await operation

    async def hit(self, scope: Scope, send: Send, body: bytes) -> None:
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        validators = Headers(scope=scope).get("if-none-match", "").split(",")
        unchanged = any(value.strip().removeprefix("W/") in (etag, "*")
                        for value in validators)
        headers = [(b"content-type", b"application/json"), (b"etag", etag.encode()),
                   (b"cache-control", b"no-cache"), (b"vary", b"Accept-Language"),
                   (b"x-api-cache", b"HIT")]
        if not unchanged:
            headers.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": 304 if unchanged else 200,
                    "headers": headers})
        await send({"type": "http.response.body", "body": b"" if unchanged else body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        key = None
        lock_token = uuid.uuid4().hex
        owns_lock = False
        cached_body = None
        if self.redis is not None and public_request(scope):
            try:
                revision_reader = (self.story_map_revision_reader if scope["path"] == "/story-map"
                                   else self.revision_reader)
                key = cache_key(scope, await revision_reader(), self.namespace)
                cached_body = await self.command(self.redis.get(key))
                story_map = scope["path"] == "/story-map"
                lock_ttl = 60 if story_map else 30
                if cached_body is None:
                    owns_lock = bool(await self.command(self.redis.set(
                        key + ":lock", lock_token, nx=True, ex=lock_ttl)))
                if cached_body is None and not owns_lock:
                    # Let the first reader populate a cold key. Slow queries
                    # eventually fall back to origin rather than blocking users.
                    for _ in range(150 if story_map else 30):
                        await asyncio.sleep(0.2 if story_map else 0.1)
                        cached_body = await self.command(self.redis.get(key))
                        if cached_body is not None:
                            break
                        if story_map and not await self.command(self.redis.get(key + ":lock")):
                            # The builder failed or returned an uncacheable response.
                            # Let one waiter retry rather than making everyone wait.
                            owns_lock = bool(await self.command(self.redis.set(
                                key + ":lock", lock_token, nx=True, ex=lock_ttl)))
                            if owns_lock:
                                break
                if owns_lock:
                    # The previous builder can finish between our GET and SET NX.
                    cached_body = await self.command(self.redis.get(key))
                    if cached_body is not None:
                        await self.command(cast(Awaitable[Any], self.redis.eval(
                            "if redis.call('get', KEYS[1]) == ARGV[1] then "
                            "return redis.call('del', KEYS[1]) else return 0 end",
                            1, key + ":lock", lock_token)))
                        owns_lock = False
            except (RedisError, TimeoutError, SQLAlchemyError, RuntimeError, UnicodeError):
                # Revision lookup failures also fail open (e.g. rolling migration).
                # Do not log exceptions containing DB/Redis connection credentials.
                self.warn()
                key = None
                cached_body = None

        if cached_body is not None:
            # Client disconnects must propagate, never restart the origin response
            # after some cached bytes have already been sent.
            await self.hit(scope, send, cached_body)
            return

        collected = bytearray()
        cacheable = False

        async def capture(message: Message) -> None:
            nonlocal cacheable
            if message["type"] == "http.response.start":
                headers = Headers(raw=message.get("headers", []))
                cacheable = (
                    owns_lock and key is not None and message["status"] == 200
                    and headers.get("content-type", "").startswith("application/json")
                    and "set-cookie" not in headers
                    and not any(word in headers.get("cache-control", "").lower()
                                for word in ("private", "no-store"))
                    and not headers.get("vary")
                )
                # API is revalidated, not held indefinitely in browser/CDN caches.
                # Personalized and unknown endpoints are explicitly private.
                if "cache-control" not in headers:
                    message["headers"] = list(message.get("headers", [])) + [
                        (b"cache-control", b"no-cache" if public_request(scope) and "set-cookie" not in headers
                         else b"private, no-store")]
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-api-cache", b"MISS" if key else b"BYPASS")]
            elif message["type"] == "http.response.body" and cacheable:
                chunk = message.get("body", b"")
                if len(collected) + len(chunk) > self.settings.api_cache_max_body_bytes:
                    cacheable = False
                    collected.clear()
                else:
                    collected.extend(chunk)
                    if not message.get("more_body", False) and self.redis is not None:
                        assert key is not None
                        try:
                            await self.command(self.redis.set(
                                key, bytes(collected), ex=self.settings.api_cache_ttl_seconds))
                        except (RedisError, TimeoutError):
                            self.warn()
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            if owns_lock and key is not None and self.redis is not None:
                try:
                    await self.command(cast(Awaitable[Any], self.redis.eval(
                        "if redis.call('get', KEYS[1]) == ARGV[1] then "
                        "return redis.call('del', KEYS[1]) else return 0 end",
                        1, key + ":lock", lock_token)))
                except (RedisError, TimeoutError):
                    self.warn()
