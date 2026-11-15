from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from wuwa_story.admin.cutscenes import CutsceneData
from wuwa_story.api.routes.cutscene_analysis import _asset_analysis_summaries, assets


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, *, execute_batches=None, scalar_batches=None):
        self._execute_batches = list(execute_batches or [])
        self._scalar_batches = list(scalar_batches or [])

    async def execute(self, statement):
        rows = self._execute_batches.pop(0) if self._execute_batches else []
        return _FakeResult(rows)

    async def scalars(self, statement):
        if not self._scalar_batches:
            return []
        return self._scalar_batches.pop(0)


@pytest.mark.asyncio
async def test_asset_analysis_summaries_aggregates_reports_previews_atlases_and_runs():
    video_a = SimpleNamespace(
        owner_node_id=101,
        file_id=501,
        metadata_json={"asset_version": "3.7.1"},
    )
    video_b = SimpleNamespace(
        owner_node_id=102,
        file_id=502,
        metadata_json={"asset_version": "3.7.1"},
    )
    ref_rows = [
        SimpleNamespace(
            owner_node_id=101,
            reference_type="cutscene_visual_description",
            metadata_json={"asset_version": "3.7.1"},
        ),
        SimpleNamespace(
            owner_node_id=101,
            reference_type="cutscene_preview",
            metadata_json={"source_file_id": 501},
        ),
        SimpleNamespace(
            owner_node_id=101,
            reference_type="video_frame_atlas",
            metadata_json={"asset_version": "3.7.1"},
        ),
        SimpleNamespace(
            owner_node_id=102,
            reference_type="cutscene_preview",
            metadata_json={"source_file_id": 502},
        ),
    ]
    run_rows = [
        SimpleNamespace(
            target_node_id=101,
            status="completed",
            metadata_json={"asset_version": "3.7.1"},
        ),
    ]
    session = _FakeSession(scalar_batches=[ref_rows, run_rows])

    summaries = await _asset_analysis_summaries(
        session,
        [(video_a, "asset:a"), (video_b, "asset:b")],
    )

    assert summaries[(101, "3.7.1")] == {
        "reports_count": 1,
        "previews_count": 1,
        "atlases_count": 1,
        "runs_count": 1,
        "latest_run_status": "completed",
        "has_analysis": True,
        "has_artifacts": True,
    }
    assert summaries[(102, "3.7.1")] == {
        "reports_count": 0,
        "previews_count": 1,
        "atlases_count": 0,
        "runs_count": 0,
        "latest_run_status": None,
        "has_analysis": False,
        "has_artifacts": True,
    }


@pytest.mark.asyncio
async def test_assets_endpoint_includes_analysis_summary_fields(monkeypatch):
    monkeypatch.setattr(
        "wuwa_story.api.routes.cutscene_analysis.get_agent_settings",
        lambda: SimpleNamespace(vision_enabled=True),
    )
    video_ref = SimpleNamespace(
        id=99,
        owner_node_id=101,
        file_id=501,
        metadata_json={"asset_version": "3.7.1"},
    )
    session = _FakeSession(
        execute_batches=[
            [(video_ref, "asset:ue:/Game/Aki/Sequence/Test")],
            [(101, 15536, "Start", "M0206_Mp4")],
            [],
        ],
        scalar_batches=[[], []],
    )

    payload = await assets(q="Start", before=None, analyzed_only=True, limit=30, session=session)

    assert payload["enabled"] is True
    assert payload["assets"] == [
        {
            "id": 101,
            "reference": "asset:ue:/Game/Aki/Sequence/Test",
            "game_version": "3.7.1",
            "cutscenes": [{"node_id": 15536, "cg_name": "Start", "cg_file": "M0206_Mp4"}],
            "reports_count": 0,
            "previews_count": 0,
            "atlases_count": 0,
            "runs_count": 0,
            "latest_run_status": None,
            "has_analysis": False,
            "has_artifacts": False,
        }
    ]


@pytest.mark.asyncio
async def test_cutscene_data_reports_previews_without_ai_analysis(monkeypatch):
    scene = SimpleNamespace(node_id=15536, cg_name="Start")
    db = SimpleNamespace(
        get=AsyncMock(return_value=scene),
        scalars=AsyncMock(side_effect=[[], []]),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/console/cutscene-data/15536",
            "path_params": {"node_id": 15536},
            "query_string": b"locale=en",
            "headers": [],
            "state": {"session": db},
        }
    )

    monkeypatch.setattr(
        "wuwa_story.admin.cutscenes.release",
        AsyncMock(return_value=SimpleNamespace(id=7, game_version="3.7.1")),
    )
    monkeypatch.setattr(
        "wuwa_story.admin.cutscenes.detail",
        AsyncMock(
            return_value={
                "game_version": "3.7.1",
                "event": {
                    "playback": {
                        "asset_version": "3.7.1",
                        "media": {"m1": {"asset_node_id": 4801}},
                    }
                },
            }
        ),
    )
    monkeypatch.setattr(
        "wuwa_story.admin.cutscenes.previews",
        AsyncMock(
            return_value={
                15536: {
                    "previews": [
                        {"file_id": 1, "url": "https://cdn.example/p1.jpg"},
                        {"file_id": 2, "url": "https://cdn.example/p2.jpg"},
                    ]
                }
            }
        ),
    )
    monkeypatch.setattr(
        "wuwa_story.admin.cutscenes.visual_reference",
        AsyncMock(return_value=None),
    )

    monkeypatch.setattr(
        "wuwa_story.admin.cutscenes.get_agent_settings",
        lambda: SimpleNamespace(vision_enabled=True),
    )

    response = await CutsceneData("Cutscene data").render(request, templates=None)
    assert response.status_code == 200
    import json

    body = json.loads(response.body)
    assert body["analysis"] == {
        "asset_node_ids": [4801],
        "asset_version": "3.7.1",
        "vision_enabled": True,
        "has_analysis": False,
        "has_artifacts": True,
        "reports_count": 0,
        "runs_count": 0,
        "latest_run_status": None,
        "previews_count": 2,
        "atlases_count": 0,
    }
