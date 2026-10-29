import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Response
from redis.exceptions import ConnectionError

from wuwa_story.api.cache import PublicResponseCache, cache_key
from wuwa_story.config.settings import Settings


@pytest.mark.asyncio
async def test_cold_revision_lookup_is_single_flight(monkeypatch):
    from wuwa_story.api import cache
    monkeypatch.setattr(cache, "_REVISION_CACHE", {"value": "", "expires": 0.0})
    monkeypatch.setattr(cache, "_REVISION_LOCK", asyncio.Lock())
    connection = AsyncMock()
    connection.__aenter__.return_value = connection

    async def read(_):
        await asyncio.sleep(0.01)
        return "revision"

    connection.scalar.side_effect = read
    monkeypatch.setattr(cache, "engine", SimpleNamespace(connect=lambda: connection))
    assert await asyncio.gather(*(cache.content_revision() for _ in range(30))) == ["revision"] * 30
    assert connection.scalar.await_count == 1


@pytest.mark.asyncio
async def test_content_revision_refreshes_after_media_commit_within_seconds(monkeypatch):
    from wuwa_story.api import cache
    clock = [100.0]
    monkeypatch.setattr(cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(cache, "_REVISION_CACHE", {"value": "", "expires": 0.0})
    connection = AsyncMock()
    connection.__aenter__.return_value = connection
    connection.scalar.side_effect = ["before-import", "after-import"]
    monkeypatch.setattr(cache, "engine", SimpleNamespace(connect=lambda: connection))
    assert await cache.content_revision() == "before-import"
    clock[0] += 2
    assert await cache.content_revision() == "before-import"
    clock[0] += 4
    assert await cache.content_revision() == "after-import"
    assert connection.scalar.await_count == 2


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def eval(self, script, count, key, token):
        if self.values.get(key) == token:
            self.values.pop(key)
        return 1


def setup_cache(redis=None, max_bytes=1024, block=None):
    state = {"calls": 0, "revision": "initial", "story_revision": "initial",
             "response": {"data": "first"}}
    app = FastAPI()

    @app.get("/{path:path}")
    async def read(path: str, response: Response):
        state["calls"] += 1
        if block:
            await block()
        if path == "catalog/cookie":
            response.set_cookie("session", "do-not-share")
        if path == "catalog/private":
            response.headers["Cache-Control"] = "private, no-store"
        if path == "catalog/error":
            response.status_code = 500
        return state["response"]

    async def revision():
        return state["revision"]

    async def story_revision():
        return state["story_revision"]

    backend = redis if redis is not None else MemoryRedis()
    app.add_middleware(PublicResponseCache, settings=Settings(
        _env_file=None, api_cache_max_body_bytes=max_bytes), redis=backend,
        revision_reader=revision, story_map_revision_reader=story_revision)
    return app, state, backend


def test_keys_cover_query_language_revision_and_duplicate_order():
    def key(query=b"", language=b"en", revision="a"):
        return cache_key({"path": "/catalog", "query_string": query,
                          "headers": [(b"accept-language", language)]}, revision, "test")
    assert key(b"a=1&b=2") == key(b"b=2&a=1")
    assert key(b"a=1&a=2") != key(b"a=2&a=1")
    assert key(b"a=1") != key(b"a=2")
    assert key(language=b"en") != key(language=b"ja")
    assert key(revision="a") != key(revision="b")
    assert key(b"secret=query").find("secret") == -1


@pytest.mark.asyncio
async def test_hits_queries_invalidation_etags_and_ttl():
    app, state, redis = setup_cache()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        miss = await client.get("/catalog?a=1")
        assert miss.headers["x-api-cache"] == "MISS"
        assert miss.headers["cache-control"] == "no-cache"
        hit = await client.get("/catalog?a=1")
        assert hit.headers["x-api-cache"] == "HIT"
        assert hit.headers["cache-control"] == "no-cache"
        unchanged = await client.get("/catalog?a=1", headers={"If-None-Match": hit.headers["etag"]})
        assert unchanged.status_code == 304 and not unchanged.content
        assert state["calls"] == 1
        await client.get("/catalog?a=2")
        state.update(revision="updated", response={"data": "second"})
        assert (await client.get("/catalog?a=1")).json() == {"data": "second"}
        assert state["calls"] == 3
        assert all(ttl == 3600 for key, ttl in redis.ttls.items() if not key.endswith(":lock"))


@pytest.mark.parametrize("path,headers", [
    ("/auth/me", {}), ("/admin/media-jobs/1", {}), ("/health", {}),
    ("/catalog", {"Cookie": "session=one"}),
    ("/catalog", {"Authorization": "Bearer one"}),
    ("/catalog", {"Range": "bytes=0-1"}),
    ("/story-map", {"Cookie": "session=one"}),
    ("/story-map", {"Authorization": "Bearer one"}),
])
@pytest.mark.asyncio
async def test_users_and_non_public_routes_bypass(path, headers):
    app, state, _ = setup_cache()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for _ in range(2):
            result = await client.get(path, headers=headers)
            assert result.headers["x-api-cache"] == "BYPASS"
            assert "no-store" in result.headers["cache-control"]
        assert state["calls"] == 2


@pytest.mark.parametrize("path,max_bytes", [
    ("/catalog/private", 1024), ("/catalog/cookie", 1024),
    ("/catalog/error", 1024), ("/catalog", 2),
])
@pytest.mark.asyncio
async def test_sensitive_errors_and_large_bodies_never_stored(path, max_bytes):
    app, state, redis = setup_cache(max_bytes=max_bytes)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        await client.get(path)
        client.cookies.clear()
        await client.get(path)
    assert state["calls"] == 2
    assert not redis.values


@pytest.mark.asyncio
async def test_redis_failure_serves_origin():
    redis = MemoryRedis()
    redis.get = AsyncMock(side_effect=ConnectionError("offline"))
    app, _, _ = setup_cache(redis)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/catalog")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("origin_delay", [0.05, 1.2])
async def test_concurrent_cold_reads_share_one_response(origin_delay):
    async def delay():
        await asyncio.sleep(origin_delay)
    app, state, _ = setup_cache(block=delay)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        results = await asyncio.gather(*(client.get("/catalog") for _ in range(5)))
    assert state["calls"] == 1
    assert all(result.status_code == 200 for result in results)


@pytest.mark.asyncio
async def test_slow_story_map_is_built_once_for_concurrent_readers():
    async def delay():
        await asyncio.sleep(3.2)
    app, state, _ = setup_cache(block=delay)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        results = await asyncio.gather(*(client.get("/story-map") for _ in range(4)))
    assert state["calls"] == 1
    assert all(result.status_code == 200 for result in results)


@pytest.mark.asyncio
async def test_story_map_waiter_retries_when_builder_fails():
    calls = 0

    async def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(0.3)
            raise RuntimeError("builder failed")

    app, _, _ = setup_cache(block=fail_once)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.get("/story-map"), client.get("/story-map"), return_exceptions=True,
        )
    assert isinstance(first, RuntimeError)
    assert second.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
async def test_cache_rechecks_after_acquiring_lock():
    class BuilderFinished(MemoryRedis):
        async def set(self, key, value, nx=False, ex=None):
            if nx:
                self.values[key.removesuffix(":lock")] = b'{"data":"ready"}'
            return await super().set(key, value, nx=nx, ex=ex)

    redis = BuilderFinished()
    app, state, _ = setup_cache(redis)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/story-map")
    assert response.json() == {"data": "ready"}
    assert state["calls"] == 0
    assert not any(key.endswith(":lock") for key in redis.values)


@pytest.mark.asyncio
async def test_inflight_old_response_cannot_fill_new_generation():
    started, finish = asyncio.Event(), asyncio.Event()
    async def delay():
        started.set()
        await finish.wait()
    app, state, _ = setup_cache(block=delay)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        old = asyncio.create_task(client.get("/catalog"))
        await started.wait()
        state["revision"] = "new"
        finish.set()
        await old
        assert (await client.get("/catalog")).headers["x-api-cache"] == "MISS"
    assert state["calls"] == 2


@pytest.mark.asyncio
async def test_story_map_uses_its_revision_without_affecting_other_routes():
    app, state, _ = setup_cache()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/story-map")).headers["x-api-cache"] == "MISS"
        assert (await client.get("/catalog")).headers["x-api-cache"] == "MISS"
        state["revision"] = "media-change"
        assert (await client.get("/story-map")).headers["x-api-cache"] == "HIT"
        assert (await client.get("/catalog")).headers["x-api-cache"] == "MISS"
        state["story_revision"] = "quest-change"
        assert (await client.get("/story-map")).headers["x-api-cache"] == "MISS"
        assert (await client.get("/catalog")).headers["x-api-cache"] == "HIT"
        # Only the exact story-map endpoint uses the narrower revision.
        assert (await client.get("/story-map/other")).headers["x-api-cache"] == "MISS"
        state["revision"] = "another-change"
        assert (await client.get("/story-map/other")).headers["x-api-cache"] == "MISS"


@pytest.mark.asyncio
async def test_story_map_revision_single_flight_and_missing_coverage(monkeypatch):
    from wuwa_story.api import cache

    monkeypatch.setattr(cache, "_STORY_MAP_REVISION_CACHE", {"value": "", "expires": 0.0})
    monkeypatch.setattr(cache, "_STORY_MAP_REVISION_LOCK", asyncio.Lock())
    connection = AsyncMock()
    connection.__aenter__.return_value = connection
    connection.scalar.return_value = "story-revision"
    monkeypatch.setattr(cache, "engine", SimpleNamespace(connect=lambda: connection))
    assert await asyncio.gather(*(cache.story_map_revision() for _ in range(10))) == ["story-revision"] * 10
    assert connection.scalar.await_count == 1
    assert connection.scalar.await_args.args[1] == {
        "expected_count": len(cache.STORY_MAP_TABLES), "tables": list(cache.STORY_MAP_TABLES)}
    cache._STORY_MAP_REVISION_CACHE["expires"] = 0
    connection.scalar.return_value = None
    with pytest.raises(RuntimeError, match="coverage is incomplete"):
        await cache.story_map_revision()


@pytest.mark.asyncio
async def test_story_map_revision_failure_bypasses_cache():
    app, state, _ = setup_cache()
    app.user_middleware[0].kwargs["story_map_revision_reader"] = AsyncMock(side_effect=RuntimeError())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/story-map")).headers["x-api-cache"] == "BYPASS"
        assert (await client.get("/story-map")).headers["x-api-cache"] == "BYPASS"
        assert (await client.get("/catalog")).headers["x-api-cache"] == "MISS"
    assert state["calls"] == 3
