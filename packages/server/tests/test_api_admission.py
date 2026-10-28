import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from wuwa_story.api.admission import RequestAdmission
from wuwa_story.api.errors import install_error_handlers


@pytest.mark.asyncio
async def test_short_burst_waits_for_capacity_without_increasing_concurrency():
    started, release = asyncio.Event(), asyncio.Event()
    app = FastAPI()
    app.add_middleware(RequestAdmission, limit=1)
    active, peak = 0, 0

    @app.get("/read")
    async def read():
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        started.set()
        await release.wait()
        active -= 1
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        first = asyncio.create_task(client.get("/read"))
        await started.wait()
        second = asyncio.create_task(client.get("/read"))
        try:
            await asyncio.sleep(0.15)
            assert not second.done()
        finally:
            release.set()
            responses = await asyncio.gather(first, second)
        assert [response.status_code for response in responses] == [200, 200]
        assert peak == 1


@pytest.mark.asyncio
async def test_overload_rejects_without_db_work_and_keeps_health_available():
    started, release = asyncio.Event(), asyncio.Event()
    app = FastAPI()
    app.add_middleware(RequestAdmission, limit=1, wait_seconds=0.01)

    @app.get("/slow")
    async def slow():
        started.set()
        await release.wait()
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        task = asyncio.create_task(client.get("/slow"))
        await started.wait()
        try:
            response = await client.get("/slow")
            assert response.status_code == 503
            assert response.headers["retry-after"] == "2"
            assert (await client.get("/health")).status_code == 200
        finally:
            release.set()
            assert (await task).status_code == 200
        assert (await client.get("/slow")).status_code == 200


@pytest.mark.asyncio
async def test_db_disconnect_is_retryable_without_leaking_sql():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/broken")
    async def broken():
        raise DBAPIError("secret SQL", {"credential": "secret"}, Exception("closed"),
                         connection_invalidated=True)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/broken")
    assert response.status_code == 503
    assert "secret" not in response.text
