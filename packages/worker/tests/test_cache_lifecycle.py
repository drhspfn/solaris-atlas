import asyncio
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from wuwa_story.cache import CacheManager
from wuwa_story.cache import cache_lifespan as api_cache_lifespan
from wuwa_story.config.settings import Settings

from wuwa_story_worker.cache import get_cache, worker_cache_lifespan


async def test_delivery_tasks_share_pool_and_lifecycle_resets_and_closes(monkeypatch):
    from wuwa_story import cache as module

    redis = AsyncMock()
    monkeypatch.setattr(module.Redis, "from_url",
                        lambda *_args, **_kwargs: redis)
    settings = Settings(redis_url="redis://localhost:6379/0")
    with pytest.raises(RuntimeError, match="lifecycle"):
        get_cache()
    async with worker_cache_lifespan(settings) as cache:
        assert isinstance(cache, CacheManager)

        async def task():
            return get_cache()

        assert await asyncio.gather(*(task() for _ in range(10))) == [cache] * 10
        redis.aclose.assert_not_awaited()
    redis.aclose.assert_awaited_once()
    with pytest.raises(RuntimeError, match="lifecycle"):
        get_cache()


async def test_api_and_worker_share_values_and_operation_leases():
    url = os.getenv("WUWA_TEST_REDIS_URL")
    if not url:
        pytest.skip(
            "Set WUWA_TEST_REDIS_URL for live cross-runtime cache checks")
    settings = Settings(
        redis_url=url, cache_namespace="worker-cache-test-" + uuid4().hex)
    async with api_cache_lifespan(settings) as api, worker_cache_lifespan(settings):
        worker = get_cache()
        assert worker is not api and worker.redis is not api.redis
        try:
            await api.set_json("job", ["queued"], ttl=10)
            assert await worker.get_json("job") == ["queued"]
            await worker.set_json("job", {"state": "done"}, ttl=10)
            assert await api.get_json("job") == {"state": "done"}
            async with api.lock("operation", ttl=10):
                assert await worker.acquire_lock("operation", ttl=10) is None
            async with worker.lock("operation", ttl=10):
                assert await api.acquire_lock("operation", ttl=10) is None
        finally:
            await api.delete("job")
    with pytest.raises(RuntimeError, match="lifecycle"):
        get_cache()


async def test_worker_run_opens_cache_before_consuming_and_closes_on_failure(monkeypatch):
    from wuwa_story import cache as module

    from wuwa_story_worker import cli

    redis = AsyncMock()
    monkeypatch.setattr(module.Redis, "from_url",
                        lambda *_args, **_kwargs: redis)
    monkeypatch.setattr("wuwa_story_worker.cache.get_settings",
                        lambda: Settings(redis_url="redis://localhost:6379/0"))

    async def consume(*_args):
        assert get_cache().redis is redis
        raise ValueError("worker failed")

    monkeypatch.setattr(cli, "_consume", consume)
    with pytest.raises(ValueError, match="worker failed"):
        await cli._run()
    redis.aclose.assert_awaited_once()
