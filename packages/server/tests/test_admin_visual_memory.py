import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.admin.auth import InternalAuth
from wuwa_story.admin.composition import install_admin
from wuwa_story.config.settings import Settings


@pytest.mark.asyncio
async def test_visual_memory_page_is_registered_and_renders_in_admin_shell(monkeypatch):
    async def authenticated(self, request):
        return True

    monkeypatch.setattr(InternalAuth, "is_authenticated", authenticated)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    app = FastAPI()
    install_admin(app, engine, Settings(_env_file=None, app_env="test"))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/console/visual-memory")
        assert response.status_code == 200
        assert "Reference library" in response.text
        assert "Laboratory" in response.text
        assert "quality_min" in response.text
        assert "vm-model-status" in response.text
        assert "/console/statics/visual-memory.js" in response.text
        assert response.headers["cache-control"] == "private, no-store"
    await engine.dispose()


@pytest.mark.asyncio
async def test_visual_memory_page_requires_console_authentication(monkeypatch):
    async def unauthenticated(self, request):
        return False

    monkeypatch.setattr(InternalAuth, "is_authenticated", unauthenticated)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    app = FastAPI()
    install_admin(app, engine, Settings(_env_file=None, app_env="test"))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/console/visual-memory", follow_redirects=False)
        assert response.status_code == 303
        assert "/console/login" in response.headers["location"]
    await engine.dispose()
