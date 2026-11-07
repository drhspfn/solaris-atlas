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
