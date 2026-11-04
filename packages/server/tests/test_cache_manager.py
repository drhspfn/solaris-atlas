"""Shared state/leases on a real Redis; every test owns an isolated namespace."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError

from wuwa_story.cache import (
    CacheLockBusy,
    CacheLockLost,
    CacheManager,
    CacheUnavailable,
    cache_lifespan,
)
from wuwa_story.config.settings import Settings


@pytest.fixture
async def caches():
    url = os.getenv("WUWA_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set WUWA_TEST_REDIS_URL to run live Redis integration checks")
    settings = Settings(redis_url=url, cache_namespace="cache-test-" + uuid4().hex,
                        cache_timeout_seconds=2)
    async with cache_lifespan(settings) as first, cache_lifespan(settings) as second:
        await first.redis.ping()
        try:
            yield first, second, settings
        finally:
            async for key in first.redis.scan_iter(match=settings.cache_namespace + ":*"):
                await first.redis.delete(key)


async def test_bytes_json_lists_strings_namespace_and_expiry(caches):
    first, second, _ = caches
    for value in ["text", ["a", "b"], {"count": 1}, False, 0]:
        await first.set_json("answer", value, ttl=10)
        assert await second.get_json("answer") == value
    await first.set_bytes("binary", b"\x00\xff", ttl=0.05)
    assert await second.get_bytes("binary") == b"\x00\xff"
    await asyncio.sleep(0.07)
    assert await second.get_bytes("binary") is None
    assert await second.delete("answer")
    assert await first.get_json("answer") is None
    foreign = CacheManager(first.redis, namespace="unrelated-" + uuid4().hex, timeout=2)
    assert await foreign.get_bytes("binary") is None


async def test_atomic_counters_and_limit_under_concurrency(caches):
    first, second, _ = caches
    results = await asyncio.gather(*[
        (first if i % 2 else second).increment("counter", ttl=5) for i in range(80)])
    assert sorted(results) == list(range(1, 81))
    results = await asyncio.gather(*[
        (first if i % 2 else second).rate_limit("actor", limit=5, window_seconds=86400)
        for i in range(80)])
    assert sum(item.allowed for item in results) == 5
    assert all(0 <= item.remaining <= 4 and 1 <= item.retry_after <= 86400 for item in results)
    now, _ = await first.redis.time()
    expected_reset = 86400 - now % 86400
    assert abs(results[-1].retry_after - expected_reset) <= 1
    assert (await first.rate_limit("another-actor", limit=5, window_seconds=86400)).allowed
    # Value/counter/limit/lease keys cannot overwrite one another.
    await first.set_json("actor", "answer", ttl=10)
    assert not (await second.rate_limit("actor", limit=5, window_seconds=86400)).allowed


async def test_fixed_window_resets_using_redis_clock(caches):
    first, second, _ = caches
    assert (await first.rate_limit("clock", limit=1, window_seconds=1)).allowed
    assert not (await second.rate_limit("clock", limit=1, window_seconds=1)).allowed
    await asyncio.sleep(1.05)
    assert (await second.rate_limit("clock", limit=1, window_seconds=1)).allowed


async def test_independent_processes_share_exactly_five_admissions(caches):
    _, _, settings = caches
    source = """
import asyncio, json, os
from wuwa_story.cache import cache_lifespan
from wuwa_story.config.settings import Settings
async def main():
    async with cache_lifespan(Settings(redis_url=os.environ['WUWA_TEST_REDIS_URL'],
        cache_namespace=os.environ['CACHE_TEST_NAMESPACE'], cache_timeout_seconds=2)) as cache:
        result = await asyncio.gather(*[cache.rate_limit('processes', limit=5, window_seconds=86400) for _ in range(8)])
        print(json.dumps(sum(item.allowed for item in result)))
asyncio.run(main())
"""
    environment = {**os.environ, "CACHE_TEST_NAMESPACE": settings.cache_namespace}
    processes = [await asyncio.create_subprocess_exec(sys.executable, "-c", source, env=environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE) for _ in range(2)]
    results = await asyncio.gather(*(process.communicate() for process in processes))
    for process, (_, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stderr.decode()
    assert sum(json.loads(stdout) for stdout, _ in results) == 5


async def test_lock_contention_expiry_and_old_owner_cannot_unlock_new_owner(caches):
    first, second, _ = caches
    old = await first.acquire_lock("job", ttl=0.05)
    assert old
    assert await second.acquire_lock("job", ttl=10) is None
    assert not await second.release_lock("job", "not-the-owner")
    await asyncio.sleep(0.07)
    new = await second.acquire_lock("job", ttl=10)
    assert new and new != old
    assert not await first.release_lock("job", old)
    assert await first.acquire_lock("job", ttl=10) is None
    assert await second.release_lock("job", new)


async def test_lock_context_releases_after_error_cancellation_and_reports_lost_lease(caches):
    first, second, _ = caches
    with pytest.raises(ValueError, match="job failed"):
        async with first.lock("job", ttl=10):
            with pytest.raises(CacheLockBusy):
                async with second.lock("job", ttl=10):
                    pytest.fail("Lock overlap")
            raise ValueError("job failed")
    async with second.lock("job", ttl=10):
        pass
    started = asyncio.Event()

    async def operation():
        async with first.lock("job", ttl=10):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(operation())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with second.lock("job", ttl=10):
        pass
    with pytest.raises(CacheLockLost):
        async with first.lock("job", ttl=0.02):
            await asyncio.sleep(0.04)


async def test_lifecycle_closes_its_pool_and_closed_manager_cannot_be_reused(monkeypatch):
    from wuwa_story import cache as module
    redis = AsyncMock()
    monkeypatch.setattr(module.Redis, "from_url", lambda *_args, **_kwargs: redis)
    async with cache_lifespan(Settings(redis_url="redis://localhost:6379/0")) as cache:
        assert cache.redis is redis
        redis.aclose.assert_not_awaited()
    redis.aclose.assert_awaited_once()
    with pytest.raises(CacheUnavailable):
        await cache.get_json("closed")


async def test_failures_are_bounded_and_credentials_never_logged(caplog):
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("redis://user:secret@example.com private-key")
    cache = CacheManager(redis, namespace="test", timeout=0.02)
    with pytest.raises(CacheUnavailable):
        await cache.get_bytes("private-key")
    assert "secret" not in caplog.text and "private-key" not in caplog.text

    async def hung(*_args):
        await asyncio.sleep(10)

    redis.get.side_effect = hung
    with pytest.raises(CacheUnavailable):
        await asyncio.wait_for(cache.get_bytes("key"), timeout=1)
    assert caplog.text.count("cache.unavailable") == 1
    with pytest.raises(CacheUnavailable):
        await CacheManager(None, namespace="test", timeout=1).get_json("key")


@pytest.mark.parametrize("ttl", [0, -1, float("nan"), float("inf")])
async def test_invalid_ttl_is_rejected_before_io(ttl):
    redis = AsyncMock()
    cache = CacheManager(redis, namespace="test", timeout=1)
    with pytest.raises(ValueError):
        await cache.set_json("key", [], ttl=ttl)
    with pytest.raises(ValueError):
        await cache.acquire_lock("key", ttl=ttl)
    redis.set.assert_not_awaited()


async def test_public_chat_shares_quota_between_app_instances(caches, monkeypatch):
    import httpx

    from wuwa_story.agents import chat
    first, second, _ = caches
    run_chat = AsyncMock(return_value={"reply": "done", "request_id": "test"})
    monkeypatch.setattr(chat, "run_chat", run_chat)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(chat_app(first),
            client=("198.51.100.23", 1234)), base_url="http://test") as one, \
        httpx.AsyncClient(transport=httpx.ASGITransport(chat_app(second),
            client=("198.51.100.23", 1234)), base_url="http://test") as two:
        responses = await asyncio.gather(*[
            (one if i % 2 else two).post("/search/chat",
                json={"messages": [{"role": "user", "content": "Find Story"}]}) for i in range(20)])
    assert sum(response.status_code == 200 for response in responses) == 5
    assert sum(response.status_code == 429 for response in responses) == 15
    assert run_chat.await_count == 5
    assert all(int(r.headers["retry-after"]) > 0 for r in responses if r.status_code == 429)
    # IPv4-mapped IPv6 is the same actor, not a fresh five-call allowance.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(chat_app(second),
            client=("::ffff:198.51.100.23", 1234)), base_url="http://test") as mapped:
        assert (await mapped.post("/search/chat", json={"messages": [
            {"role": "user", "content": "Again"}]})).status_code == 429


def chat_app(cache):
    from fastapi import FastAPI

    from wuwa_story.api.errors import install_error_handlers
    from wuwa_story.api.routes.search import router
    from wuwa_story.db.session import get_session

    app = FastAPI()
    app.state.cache = cache
    app.include_router(router)
    install_error_handlers(app)

    async def session():
        yield object()

    app.dependency_overrides[get_session] = session
    return app


@pytest.mark.parametrize("client,enabled", [(("198.51.100.23", 1234), False), (None, True)])
async def test_public_chat_does_not_invoke_model_without_quota_or_client(client, enabled, monkeypatch):
    import httpx

    from wuwa_story.agents import chat

    run_chat = AsyncMock()
    monkeypatch.setattr(chat, "run_chat", run_chat)
    redis = AsyncMock() if enabled else None
    app = chat_app(CacheManager(redis, namespace="test", timeout=1))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=client),
                                base_url="http://test") as caller:
        response = await caller.post("/search/chat", json={"messages": [
            {"role": "user", "content": "Find Story"}]})
    assert response.status_code == 503
    assert response.headers["retry-after"]
    run_chat.assert_not_awaited()
