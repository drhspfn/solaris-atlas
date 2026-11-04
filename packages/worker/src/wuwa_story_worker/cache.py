"""One cache pool per worker event loop, inherited by delivery/background tasks."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from wuwa_story.cache import CacheManager, cache_lifespan
from wuwa_story.config.settings import Settings, get_settings

_cache: ContextVar[CacheManager] = ContextVar("worker_cache")


def get_cache() -> CacheManager:
    try:
        return _cache.get()
    except LookupError:
        raise RuntimeError("Worker cache lifecycle is not running") from None


@asynccontextmanager
async def worker_cache_lifespan(settings: Settings | None = None) -> AsyncIterator[CacheManager]:
    async with cache_lifespan(settings or get_settings()) as cache:
        token = _cache.set(cache)
        try:
            yield cache
        finally:
            _cache.reset(token)
