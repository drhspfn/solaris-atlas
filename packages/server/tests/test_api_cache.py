import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Response
from redis.exceptions import ConnectionError

from wuwa_story.api.cache import PublicResponseCache, cache_key
from wuwa_story.config.settings import Settings


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
    state = {"calls": 0, "revision": "initial", "response": {"data": "first"}}
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

    backend = redis if redis is not None else MemoryRedis()
    app.add_middleware(PublicResponseCache, settings=Settings(
        _env_file=None, api_cache_max_body_bytes=max_bytes), redis=backend,
        revision_reader=revision)
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
        assert (await client.get("/catalog?a=1")).headers["x-api-cache"] == "MISS"
        hit = await client.get("/catalog?a=1")
        assert hit.headers["x-api-cache"] == "HIT"
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
async def test_concurrent_cold_reads_share_one_response():
    async def delay():
        await asyncio.sleep(0.05)
    app, state, _ = setup_cache(block=delay)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        results = await asyncio.gather(*(client.get("/catalog") for _ in range(5)))
    assert state["calls"] == 1
    assert all(result.status_code == 200 for result in results)


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
