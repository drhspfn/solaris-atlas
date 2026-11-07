import pytest
from fastapi.testclient import TestClient

from wuwa_story.api.app import app, settings


def test_health_is_available_without_database() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_swagger_targets_configured_api_from_frontend_origin() -> None:
    client = TestClient(app, base_url="http://localhost:3000")
    schema = client.get("/openapi.json").json()
    assert schema["servers"] == [{"url": settings.api_url.rstrip("/")}]
    docs = client.get("/docs")
    assert '"withCredentials": true' in docs.text
    origin = settings.cors_origins[0]
    health = client.get("/health", headers={"Origin": origin})
    assert health.status_code == 200
    assert health.headers["access-control-allow-origin"] == origin
    assert health.headers["access-control-allow-credentials"] == "true"


def test_mcp_streamable_http_endpoint_lists_tools() -> None:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(app) as client:
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        assert initialized.status_code == 200
        session_id = initialized.headers["mcp-session-id"]

        listed = client.post(
            "/mcp",
            headers={**headers, "Mcp-Session-Id": session_id, "Mcp-Protocol-Version": "2025-03-26"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

    assert listed.status_code == 200
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
        "search",
        "get_node",
        "get_node_related",
        "get_node_edges",
        "get_node_narrative_context",
        "get_quest_transcript",
        "search_lore",
        "get_character_timeline",
        "get_quest_profile", "get_quest_media",
        "get_character_profile", "get_character_archive", "get_character_skills",
        "get_character_model", "get_character_connections",
        "get_item_profile", "get_item_sellers", "search_cutscenes", "get_cutscene",
    }


@pytest.mark.asyncio
async def test_mcp_tool_uses_configured_async_session_factory(monkeypatch) -> None:
    from wuwa_story.api.routes import mcp

    class FakeSession:
        async def scalars(self, _statement):
            return []

    class FakeSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *_args):
            return None

    from wuwa_story.db import session

    monkeypatch.setattr(session, "SessionFactory", FakeSessionFactory())
    result = await mcp.handle_call_tool("get_quest", {"quest_id": "test-quest"})

    assert result[0].text == "Quest 'test-quest' not found."

@pytest.mark.asyncio
async def test_mcp_quest_profile_passes_snapshot_and_locale(monkeypatch) -> None:
    import json

    from wuwa_story.api.routes import mcp
    from wuwa_story.api.routes.story import profiles
    from wuwa_story.db import session

    class Factory:
        def __call__(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None

    received = {}
    async def profile(**kwargs):
        received.update(kwargs)
        return {"quest": {"game_quest_id": 123}}

    monkeypatch.setattr(session, "SessionFactory", Factory())
    monkeypatch.setattr(profiles, "quest_profile", profile)
    result = await mcp.handle_call_tool("get_quest_profile", {"quest_id": "123", "locale": "ja", "game_version": "3.3.0"})
    assert json.loads(result[0].text)["quest"]["game_quest_id"] == 123
    assert received["locale"] == "ja"
    assert received["game_version"] == "3.3.0"


@pytest.mark.asyncio
async def test_mcp_dispatches_character_tool(monkeypatch) -> None:
    import json
    from unittest.mock import AsyncMock

    from wuwa_story.api.routes import mcp, mcp_character_tools
    from wuwa_story.db import session

    class Factory:
        def __call__(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None

    monkeypatch.setattr(session, "SessionFactory", Factory())
    dispatch = AsyncMock(return_value={"skills": [{"name": "Attack", "values": [10, 20]}]})
    monkeypatch.setattr(mcp_character_tools, "dispatch", dispatch)
    response = await mcp.handle_call_tool("get_character_skills", {"canonical_key": "character:1211", "locale": "ja"})
    assert json.loads(response[0].text)["skills"][0]["values"] == [10, 20]
    assert dispatch.await_args.args[:2] == ("get_character_skills", {"canonical_key": "character:1211", "locale": "ja"})
