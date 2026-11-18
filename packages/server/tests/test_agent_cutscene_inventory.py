from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.agents import evidence


@pytest.mark.asyncio
async def test_inventory_includes_inferred_scenario_and_prefers_explicit(monkeypatch):
    monkeypatch.setattr(evidence, "quest_states", AsyncMock(return_value=[20]))
    session = SimpleNamespace(
        scalars=AsyncMock(side_effect=[[20, 21], [9]]),
        execute=AsyncMock(side_effect=[[(1, 100, "asset:a")],
                                       [(1, 100, "asset:a"), (2, 101, "asset:b")]]),
    )
    assert await evidence.quest_cutscene_assets(session, 5, 7) == [
        (1, 100, "asset:a", False), (2, 101, "asset:b", True),
    ]

    tools = evidence.EvidenceTools(session, run_id=1, quest=SimpleNamespace(node_id=5),
                                   release_id=7, locale=SimpleNamespace(), settings=SimpleNamespace())
    monkeypatch.setattr(evidence, "quest_cutscene_assets", AsyncMock(return_value=[(2, 101, "asset:b", True)]))
    monkeypatch.setattr(evidence, "visual_reference", AsyncMock(return_value=None))
    monkeypatch.setattr(evidence, "quest_visual_reference", AsyncMock(return_value=None))
    inventory = await tools.cutscene_inventory(tools.quest)
    assert inventory[0]["context_inferred"] is True
    assert inventory[0]["quest_association"] == "possible"
    assert "do not assert its occurrence or order" in inventory[0]["context_warning"]
    monkeypatch.setattr(tools, "remember_node", AsyncMock())
    missing_visual = await tools.read_cutscene_visual(101)
    assert missing_visual["status"] == "not_analyzed"
    assert missing_visual["quest_association"] == "possible"
    assert missing_visual["context_inferred"] is True


@pytest.mark.asyncio
async def test_scene_report_only_used_for_selected_published_variant(monkeypatch):
    from wuwa_story.agents import scene_analysis_reader
    from wuwa_story.api.routes import cutscene_analysis

    session = SimpleNamespace(scalars=AsyncMock(return_value=[500]),
                              get=AsyncMock(return_value=SimpleNamespace(game_version="3.8.0")))
    legacy = SimpleNamespace(id=11)
    scene_report = SimpleNamespace(id=12)
    monkeypatch.setattr(evidence, "visual_reference", AsyncMock(return_value=legacy))
    resolve = AsyncMock(return_value=({"selection": {"selected_asset_node_ids": [100]}}, None, {}))
    monkeypatch.setattr(cutscene_analysis, "_logical_plan", resolve)
    lookup = AsyncMock(return_value=scene_report)
    monkeypatch.setattr(scene_analysis_reader, "current_scene_report", lookup)

    assert await evidence.quest_visual_reference(session, 1, 100, 7) is scene_report
    assert await evidence.quest_visual_reference(session, 2, 101, 7) is legacy
    lookup.assert_awaited_once()
    session.scalars.return_value = [500, 501]
    assert await evidence.quest_visual_reference(session, 1, 100, 7) is legacy
    assert resolve.await_count == 2


@pytest.mark.asyncio
async def test_visual_reader_returns_timeline_and_uses_available_variant(monkeypatch):
    metadata = {"asset_version": "3.8.0", "evidence_type": "sampled_video", "duration": 10,
                "events": [{"time": 2}], "story_events": [{"time_start": 2, "time_end": 4}],
                "identity_tracks": [{"identity": "Lily", "appearance": "present"}]}
    report = SimpleNamespace(id=12, metadata_json=metadata)
    tools = evidence.EvidenceTools(SimpleNamespace(), run_id=1, quest=SimpleNamespace(node_id=5),
                                   release_id=7, locale=SimpleNamespace(), settings=SimpleNamespace())
    monkeypatch.setattr(tools, "remember_node", AsyncMock())
    monkeypatch.setattr(tools, "cutscene_inventory", AsyncMock(return_value=[
        {"asset_node_id": 100, "variant_node_id": 1, "visual_reference_id": None},
        {"asset_node_id": 100, "variant_node_id": 2, "visual_reference_id": 12,
         "context_inferred": False, "quest_association": "source_linked", "context_warning": None},
    ]))
    lookup = AsyncMock(return_value=report)
    monkeypatch.setattr(evidence, "quest_visual_reference", lookup)
    result = await tools.read_cutscene_visual(100)
    assert result["story_events"] == metadata["story_events"]
    assert result["identity_tracks"] == metadata["identity_tracks"]
    assert lookup.await_args.args[1] == 2
