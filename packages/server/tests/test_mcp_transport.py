import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from wuwa_story.api.routes import mcp


def test_transcript_requests_work_across_independent_workers(monkeypatch) -> None:
    from wuwa_story.api.routes.story import transcripts
    from wuwa_story.db import session

    class Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    async def transcript(**kwargs):
        assert kwargs["game_quest_id"] == 119000001
        assert kwargs["limit"] == 100
        assert kwargs["include_raw"] is False
        return {"offset": kwargs["offset"], "lines": [f"line-{kwargs['offset']}"]}

    monkeypatch.setattr(session, "SessionFactory", Factory())
    monkeypatch.setattr(transcripts, "quest_transcript", transcript)

    def worker():
        manager = StreamableHTTPSessionManager(
            app=mcp.mcp, json_response=True, stateless=mcp.streamable_http.stateless
        )

        @asynccontextmanager
        async def lifespan(_app):
            async with manager.run():
                yield

        app = FastAPI(lifespan=lifespan)
        app.mount("/mcp", manager.handle_request)
        return app

    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(worker()) as first, TestClient(worker()) as second:
        initialized = first.post("/mcp/", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        assert initialized.status_code == 200
        session_id = initialized.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        headers["Mcp-Protocol-Version"] = "2025-03-26"
        for offset, client in [(0, first), (100, first), (200, second)]:
            request_headers = dict(headers)
            if offset == 200 and not session_id:
                request_headers["Mcp-Session-Id"] = "previous-session"
            response = client.post("/mcp/", headers=request_headers, json={
                "jsonrpc": "2.0", "id": offset + 2, "method": "tools/call",
                "params": {"name": "get_quest_transcript", "arguments": {
                    "quest_id": 119000001, "offset": offset, "limit": 100,
                }},
            })
            assert response.status_code == 200
            content = response.json()["result"]["content"]
            assert json.loads(content[0]["text"])["offset"] == offset
