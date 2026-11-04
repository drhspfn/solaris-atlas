"""Shared Redis values, counters and bounded operation leases for API and workers."""

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar, cast
from uuid import uuid4

from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from wuwa_story.config.settings import Settings

logger = logging.getLogger(__name__)
T = TypeVar("T")
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_INCREMENT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return n
"""
_LIMIT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1])
local window = tonumber(ARGV[2])
local bucket = math.floor(now / window)
local reset = (bucket + 1) * window
local limit = tonumber(ARGV[1])
local counts = {}
local highest = 0
for i, key in ipairs(KEYS) do
    local saved = tonumber(redis.call('HGET', key, 'bucket'))
    local count = 0
    if saved == bucket then count = tonumber(redis.call('HGET', key, 'count')) or 0 end
    if count >= limit then return {0, limit, reset - now} end
    counts[i] = count + 1
    highest = math.max(highest, counts[i])
end
-- Validate every quota before consuming any of them.
for i, key in ipairs(KEYS) do
    redis.call('HSET', key, 'bucket', bucket, 'count', counts[i])
    redis.call('EXPIREAT', key, reset)
end
return {1, highest, reset - now}
"""


class CacheUnavailable(Exception):
    """No backend, timeout, connection failure or a rejected Redis operation."""


class CacheLockBusy(Exception):
    """Another caller currently owns the operation lease."""


class CacheLockLost(Exception):
    """The operation outlived its lease or Redis lost its ownership record."""


@dataclass(frozen=True)
class RateLimit:
    allowed: bool
    remaining: int
    retry_after: int


class CacheManager:
    def __init__(self, redis: Redis | None, *, namespace: str, timeout: float) -> None:
        if not namespace or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("A cache namespace and positive timeout are required")
        # Exposed only for existing Redis-specific response/vector adapters. New
        # consumers use the bounded, namespaced methods below.
        self.redis = redis
        self.namespace = namespace
        self.timeout = timeout
        self._last_warning = -math.inf
        self._closed = False

    def _key(self, kind: str, key: str) -> str:
        if not key:
            raise ValueError("Cache key must not be empty")
        digest = hashlib.sha256(key.encode()).hexdigest()
        return f"{self.namespace}:{kind}:{digest}"

    @staticmethod
    def _ttl(seconds: float) -> int:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("TTL must be finite and positive")
        return max(1, math.ceil(seconds * 1000))

    async def _command(self, operation: Callable[[Redis], Awaitable[T]]) -> T:
        try:
            if self.redis is None or self._closed:
                raise CacheUnavailable
            async with asyncio.timeout(self.timeout):
                return await operation(self.redis)
        except (RedisError, TimeoutError, CacheUnavailable):
            now = time.monotonic()
            if now - self._last_warning >= 60:
                logger.warning("cache.unavailable namespace=%s", self.namespace)
                self._last_warning = now
            # Never expose credentials, keys, payloads or Redis exception messages.
            raise CacheUnavailable("Shared cache is temporarily unavailable") from None

    async def get_bytes(self, key: str) -> bytes | None:
        return cast(bytes | None, await self._command(lambda r: r.get(self._key("value", key))))

    async def set_bytes(self, key: str, value: bytes, *, ttl: float) -> None:
        milliseconds = self._ttl(ttl)
        await self._command(lambda r: r.set(self._key("value", key), value, px=milliseconds))

    async def get_json(self, key: str) -> Any:
        value = await self.get_bytes(key)
        return json.loads(value) if value is not None else None

    async def set_json(self, key: str, value: Any, *, ttl: float) -> None:
        await self.set_bytes(key, json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), ttl=ttl)

    async def delete(self, key: str) -> bool:
        return bool(await self._command(lambda r: r.delete(self._key("value", key))))

    async def increment(self, key: str, *, ttl: float) -> int:
        """Atomic increment; the first increment starts the expiring window."""
        milliseconds = self._ttl(ttl)
        return int(await self._command(lambda r: cast(Awaitable[Any], r.eval(
            _INCREMENT, 1, self._key("counter", key), str(milliseconds)))))

    async def rate_limit(self, key: str, *, limit: int, window_seconds: int) -> RateLimit:
        """Fixed UTC/epoch-aligned window, evaluated atomically on Redis's clock."""
        return await self.rate_limits([key], limit=limit, window_seconds=window_seconds)

    async def rate_limits(self, keys: Sequence[str], *, limit: int, window_seconds: int) -> RateLimit:
        """Admit only if every quota permits it; denied requests consume none."""
        if limit < 1 or window_seconds < 1:
            raise ValueError("Limit and window must be positive")
        redis_keys = [self._key("limit", key) for key in dict.fromkeys(keys)]
        if not redis_keys:
            raise ValueError("At least one quota key is required")
        result = await self._command(lambda r: cast(Awaitable[Any], r.eval(
            _LIMIT, len(redis_keys), *redis_keys, str(limit), str(window_seconds))))
        allowed, count, retry_after = map(int, result)
        return RateLimit(bool(allowed), max(0, limit - count), retry_after)

    async def acquire_lock(self, key: str, *, ttl: float) -> str | None:
        """Return an ownership token, or None if busy. Never wait indefinitely."""
        milliseconds, token = self._ttl(ttl), uuid4().hex
        acquired = await self._command(lambda r: r.set(
            self._key("lock", key), token, nx=True, px=milliseconds))
        return token if acquired else None

    async def release_lock(self, key: str, token: str) -> bool:
        return bool(await self._command(lambda r: cast(Awaitable[Any], r.eval(
            _RELEASE, 1, self._key("lock", key), token))))

    @asynccontextmanager
    async def lock(self, key: str, *, ttl: float) -> AsyncIterator[None]:
        """A finite lease: callers must finish within TTL; no automatic renewal."""
        token = await self.acquire_lock(key, ttl=ttl)
        if token is None:
            raise CacheLockBusy("Operation is already in progress")
        try:
            yield
        except BaseException:
            try:
                await self.release_lock(key, token)
            except CacheUnavailable:
                pass
            # Cleanup must not replace the operation's original error/cancellation.
            raise
        else:
            if not await self.release_lock(key, token):
                raise CacheLockLost("Operation lease expired or ownership was lost")

    async def aclose(self) -> None:
        self._closed = True
        if self.redis is not None:
            await self.redis.aclose()


@asynccontextmanager
async def cache_lifespan(settings: Settings) -> AsyncIterator[CacheManager]:
    redis = Redis.from_url(settings.redis_url.get_secret_value(),
        socket_connect_timeout=settings.cache_timeout_seconds,
        socket_timeout=settings.cache_timeout_seconds, max_connections=50,
        retry=Retry(NoBackoff(), 0)) if settings.redis_url else None
    cache = CacheManager(redis, namespace=settings.cache_namespace, timeout=settings.cache_timeout_seconds)
    try:
        yield cache
    finally:
        await cache.aclose()
