from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.agents.scene_analysis_reader import current_scene_report


@pytest.mark.asyncio
async def test_scene_report_requires_entire_current_publication_plan():
    plan = {"version": 1, "cutscene_node_id": 15536,
            "selection": {"flow_reference_id": 11, "rover": "female"},
            "segments": [{"asset_node_id": 10013, "file_id": 50, "source_sha256": "a" * 64,
                          "asset_version": "3.7.1", "scene_start": 0, "scene_end": 12},
                         {"asset_node_id": 10014, "file_id": 51, "source_sha256": "b" * 64,
                          "asset_version": "3.7.1", "scene_start": 12, "scene_end": 20}]}
    report = SimpleNamespace(metadata_json={"cutscene_node_id": 15536, "analysis_plan": deepcopy(plan)})
    session = SimpleNamespace(scalars=AsyncMock(return_value=[report]))
    assert await current_scene_report(session, 15536, "3.7.1", plan) is report
    for change in (lambda p: p["segments"][1].update(file_id=52),
                   lambda p: p["segments"][0].update(source_sha256="c" * 64),
                   lambda p: p["selection"].update(flow_reference_id=12),
                   lambda p: p["segments"].reverse()):
        updated = deepcopy(plan)
        change(updated)
        assert await current_scene_report(session, 15536, "3.7.1", updated) is None
    assert await current_scene_report(session, 15536, "3.7.0", plan) is None
    assert await current_scene_report(session, 15537, "3.7.1", plan) is None
