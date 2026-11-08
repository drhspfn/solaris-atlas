from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes import mcp_video_tools


@pytest.mark.asyncio
async def test_public_reviewed_frame_keeps_its_sampling_scope(monkeypatch):
    scan = {"id": 7, "status": "succeeded", "reviewed": True,
            "sampling": {"sampling_mode": "frame", "timestamp_seconds": 91,
                         "frame_offset": 10, "private": "do not publish"},
            "frames": [{"index": 0, "time": 91.333333, "image_url": "/frame"}]}
    service = SimpleNamespace(
        sources=AsyncMock(return_value={"name": "CG", "sources": [{"scan": scan}]}),
        get_scan=AsyncMock(return_value=scan),
    )
    monkeypatch.setattr(mcp_video_tools, "service_for", lambda _: service)
    inspected = await mcp_video_tools.dispatch("visual_inspect_cutscene", {"cutscene_key": "CG"}, None)
    read = await mcp_video_tools.dispatch("visual_get_video_scan", {"scan_id": 7}, None)
    assert inspected["sources"][0]["reviewed_scan_id"] == 7
    assert read["sampling"] == inspected["sources"][0]["sampling"] == {
        "sampling_mode": "frame", "timestamp_seconds": 91, "frame_offset": 10,
        "interval_seconds": None,
    }
    assert "private" not in str(read) and "private" not in str(inspected)
    assert read["frames"][0]["time"] == 91.333333
