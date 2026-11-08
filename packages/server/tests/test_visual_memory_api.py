import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from wuwa_story.api.routes import visual_memory
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.session import get_session
from wuwa_story.visual_memory import tools


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(visual_memory.router)
    session = SimpleNamespace(commit=AsyncMock())
    service = SimpleNamespace(search=AsyncMock(return_value={"status": "unknown", "candidates": []}),
                              add_reference=AsyncMock(return_value={"id": 1}))
    app.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(visual_memory, "service_for", lambda _: service)
    return app, session, service


@pytest.mark.asyncio
async def test_admin_auth_csrf_validation_and_transactions(app):
    api, session, service = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get("/admin/visual-memory/models")).status_code == 401
        api.dependency_overrides[require_admin] = lambda: SimpleNamespace(
            id=42)
        payload = {"image_base64": base64.b64encode(
            b"image").decode(), "links": [{"identity_id": 4}]}
        assert (await client.post("/admin/visual-memory/references", json=payload)).status_code == 403
        api.dependency_overrides[require_csrf] = lambda: None
        assert (await client.post("/admin/visual-memory/references", json={**payload, "image_base64": "@@"})).status_code == 422
        assert (await client.post("/admin/visual-memory/references", json={**payload, "image_base64": "A" * (tools.MAX_ENCODED + 1)})).status_code == 422
        assert (await client.post("/admin/visual-memory/references", content=b"x" * (tools.MAX_ENCODED + 65537))).status_code == 413
        response = await client.post("/admin/visual-memory/references", json=payload)
        assert response.status_code == 200
        assert response.json() == {"id": 1}
        assert service.add_reference.await_args.kwargs["actor_id"] == 42
        session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_uses_service_and_never_serves_html(app):
    api, _, service = app
    api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=42)
    service.get_image_bytes = AsyncMock(return_value=b"<script>bad</script>")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get("/admin/visual-memory/references/1/image")).status_code == 415
        service.get_image_bytes.return_value = b"\x89PNG\r\n\x1a\nexample"
        result = await client.get("/admin/visual-memory/references/1/image?original=true")
        assert result.headers["content-type"] == "image/png"
        assert result.headers["x-content-type-options"] == "nosniff"
        service.get_image_bytes.assert_awaited_with(1, original=True)


@pytest.mark.asyncio
async def test_duplicate_appearance_returns_conflict(app):
    api, _, service = app
    api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=42)
    api.dependency_overrides[require_csrf] = lambda: None
    error = Exception("duplicate")
    error.sqlstate = "23505"
    service.create_appearance = AsyncMock(
        side_effect=IntegrityError("insert", {}, error))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = await client.post("/admin/visual-memory/identities/1/appearances", json={"name": "outfit"})
        assert result.status_code == 409


@pytest.mark.asyncio
async def test_public_catalog_and_capability_gate(monkeypatch):
    from wuwa_story.api.routes.mcp import handle_list_tools

    names = {tool.name for tool in await handle_list_tools()}
    assert {name for name in names if name.startswith("visual_")} == {
        "visual_search", "visual_identify", "visual_list_identities", "visual_get_reference",
        "visual_inspect_cutscene", "visual_get_video_scan"
    }
    service = SimpleNamespace(add_reference=AsyncMock(), search=AsyncMock(
        return_value={"status": "unknown", "candidates": []}))
    adapter = tools.VisualMemoryTools(service)
    with pytest.raises(PermissionError):
        await adapter.execute("visual_add_reference", {})
    assert not service.add_reference.await_count
    monkeypatch.setattr(tools, "service_for", lambda _: service)
    result = await tools.dispatch("visual_search", {"image_base64": "!!"}, None)
    assert result["error"]["code"] == "invalid_input"
    assert not service.search.await_count
    with pytest.raises(ValueError):
        await tools.dispatch("visual_remove_reference", {}, None)


@pytest.mark.asyncio
async def test_moderation_is_separate_and_public_references_are_filtered(monkeypatch):
    service = SimpleNamespace(add_reference=AsyncMock(), review_reference=AsyncMock(),
                              get_reference=AsyncMock(return_value={"status": "pending", "enabled_for_search": False, "secret": "pending"}))
    writer = tools.VisualMemoryTools(service, allow_write=True, actor_id=7)
    with pytest.raises(PermissionError):
        await writer.execute("visual_add_reference", {"image_base64": "eA==", "links": [{"identity_id": 1}], "status": "approved"})
    with pytest.raises(PermissionError):
        await writer.execute("visual_review_reference", {"reference_id": 1, "status": "approved"})
    with pytest.raises(PermissionError):
        await writer.execute("visual_remove_reference", {"reference_id": 1, "mode": "reject"})
    assert not service.add_reference.await_count and not service.review_reference.await_count
    admin = tools.VisualMemoryTools(
        service, allow_write=True, allow_moderation=True, actor_id=7)
    await admin.execute("visual_review_reference", {"reference_id": 1, "status": "approved"})
    service.review_reference.assert_awaited_once()
    monkeypatch.setattr(tools, "service_for", lambda _: service)
    result = await tools.dispatch("visual_get_reference", {"reference_id": 1}, None)
    assert "pending" not in str(
        result) and result["error"]["code"] == "invalid_input"
    service.get_reference.return_value = {
        "status": "approved", "enabled_for_search": False}
    assert "error" in await tools.dispatch("visual_get_reference", {"reference_id": 1}, None)
    service.get_reference.return_value = {
        "status": "approved", "enabled_for_search": True}
    assert (await tools.dispatch("visual_get_reference", {"reference_id": 1}, None))["status"] == "approved"
    service.get_reference.side_effect = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        await tools.dispatch("visual_get_reference", {"reference_id": 1}, None)


@pytest.mark.asyncio
async def test_bounds_and_search_argument_forwarding():
    service = SimpleNamespace(search=AsyncMock(
        return_value={"status": "unknown", "candidates": []}))
    adapter = tools.VisualMemoryTools(service)
    await adapter.execute("visual_search", {"image_base64": "eA==", "bbox": [1, 2, 3, 4], "limit": 2})
    service.search.assert_awaited_once_with(
        b"x", bbox=(1.0, 2.0, 3.0, 4.0), limit=2, kind=None)
    with pytest.raises(ValueError):
        await adapter.execute("visual_search", {"image_base64": "eA==", "limit": 11})


@pytest.mark.asyncio
async def test_chat_registry_dispatches_only_read_tools(monkeypatch):
    from wuwa_story.agents.capabilities import PUBLIC_READ_TOOLS
    from wuwa_story.agents.chat import has_evidence
    from wuwa_story.agents.chat_tools import execute_read, read_definitions
    from wuwa_story.agents.providers import ToolCall

    names = {item["name"] for item in read_definitions()}
    assert {tool.name for tool in tools.TOOLS} <= names & PUBLIC_READ_TOOLS
    assert not any(name in names for name in (
        "visual_add_reference", "visual_review_reference"))
    dispatch = AsyncMock(return_value={"items": [{"id": 1}]})
    monkeypatch.setattr(tools, "dispatch", dispatch)
    result = await execute_read(None, ToolCall("1", "visual_list_identities", {"q": "npc"}))
    assert has_evidence(result)
    dispatch.assert_awaited_once_with(
        "visual_list_identities", {"q": "npc"}, None)


def test_reference_metadata_bounds():
    payload = {"image_base64": "eA==", "links": [
        {"identity_id": 1}], "author": "Artist", "quality": 0.5}
    assert tools.ReferenceInput.model_validate(payload).quality == 0.5
    for quality in (float("nan"), float("inf"), -0.1, 1.1):
        with pytest.raises(ValueError):
            tools.ReferenceInput.model_validate(
                {**payload, "quality": quality})
