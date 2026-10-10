from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from wuwa_story.api.routes import admin_data
from wuwa_story.auth.dependencies import require_admin


def session_for(output, processor="release_media"):
    session = AsyncMock()
    session.execute.return_value.first = Mock(return_value=(SimpleNamespace(raw_output=output), processor))
    return session


def test_media_diagnostics_endpoints_require_admin():
    routes = [route for route in admin_data.router.routes if route.path.endswith(("/media-report", "/media-inventory"))]
    assert len(routes) == 2
    for route in routes:
        assert require_admin in [dep.call for dep in route.dependant.dependencies]


@pytest.mark.asyncio
async def test_report_filters_and_pages_without_exposing_worker_checkpoints():
    session = session_for({"media_report": {
        "requested": 3, "found": 1, "missing": 2, "inventory_file_id": 4, "private": "secret",
        "entries": [
            {"expected": "en_vo_A.wem", "status": "found", "matches": ["A.wem"]},
            {"expected": "ja_vo_A.wem", "status": "not_in_archives", "private": "secret"},
            {"expected": "ko_vo_A.wem", "status": "not_in_archives"},
        ],
    }, "checkpoint": "private"})
    result = await admin_data.media_report(7, offset=0, limit=1, missing_only=True, search="VO_a", session=session)
    assert result["total"] == 2 and result["next_offset"] == 1
    assert result["inventory_available"] is True
    assert result["entries"] == [{"expected": "ja_vo_A.wem", "status": "not_in_archives"}]
    assert "private" not in result["summary"] and "inventory_file_id" not in result["summary"]


@pytest.mark.asyncio
async def test_old_tasks_expose_actual_missing_names_without_inventing_matches():
    session = session_for({"missing_voices": ["en_vo_Missing"], "tracks": 392})
    result = await admin_data.media_report(7, offset=0, limit=50, missing_only=False, search="", session=session)
    assert result["legacy"] is True
    assert result["entries"] == [{"expected": "en_vo_Missing", "status": "not_resolved", "matches": []}]
    assert result["inventory_available"] is False


@pytest.mark.asyncio
async def test_report_cannot_read_agent_conversations():
    with pytest.raises(HTTPException) as error:
        await admin_data.media_report(7, offset=0, limit=50, search="", session=session_for({}, "story_agent"))
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_inventory_stream_uses_only_the_file_pinned_to_this_media_task(monkeypatch):
    session = session_for({"media_report": {"inventory_file_id": 4}})
    session.scalar.return_value = SimpleNamespace(object_key="objects/inventory")
    storage = SimpleNamespace(backend="s3", bucket="test", stat=AsyncMock(return_value=object()))
    async def chunks(key):
        assert key == "objects/inventory"
        yield b'{"path":"en_vo_A.wem"}\n'
    storage.get = chunks
    monkeypatch.setattr(admin_data, "S3Storage", lambda _: storage)
    response = await admin_data.media_inventory(7, session)
    assert response.headers["cache-control"] == "private, no-store"
    assert "media-task-7-inventory.jsonl" in response.headers["content-disposition"]
    assert b"".join([chunk async for chunk in response.body_iterator]) == b'{"path":"en_vo_A.wem"}\n'


@pytest.mark.asyncio
async def test_inventory_without_pinned_file_returns_404():
    with pytest.raises(HTTPException) as error:
        await admin_data.media_inventory(7, session_for({}))
    assert error.value.status_code == 404
