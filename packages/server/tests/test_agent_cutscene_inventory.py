import json
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


@pytest.mark.asyncio
async def test_visual_text_context_requires_matching_completed_report_and_snapshot():
    snapshot = {"basis": "source_captions", "release": {"id": 7}, "language": "en",
                "rows": [{"node_id": 20, "text": "Together?", "start_seconds": 2, "end_seconds": 3}]}
    report = SimpleNamespace(id=12, owner_node_id=500,
                             metadata_json={"run_id": 1, "source_sha256": "hash", "source_file_id": 9})
    run = SimpleNamespace(status="completed", target_node_id=500, raw_output={"report_reference_id": 12},
                          metadata_json={"file_id": 9, "source_sha256": "hash", "text_context": snapshot})
    session = SimpleNamespace(get=AsyncMock(return_value=run))
    result = await evidence.visual_text_context(session, report, 7)
    assert result["captions"][0]["text"] == "Together?"
    assert await evidence.visual_text_context(session, report, 8) is None
    run.raw_output["report_reference_id"] = 13
    assert await evidence.visual_text_context(session, report, 7) is None


@pytest.mark.asyncio
async def test_context_paginates_and_records_exact_read_coverage(monkeypatch):
    metadata = {"events": [], "asset_version": "3.8.0"}
    report = SimpleNamespace(id=12, metadata_json=metadata)
    context = {"language": "en", "coverage": "source_video", "dialogue_timing": "surrounding_state_only",
               "truncated": False, "captions": [{"node_id": 1, "text": "Together?"}],
               "dialogue_lines": [{"node_id": 2, "text": "I'll live."}]}
    tools = evidence.EvidenceTools(SimpleNamespace(), run_id=1, quest=SimpleNamespace(node_id=5),
        release_id=7, locale=SimpleNamespace(), settings=SimpleNamespace(tool_result_chars=18000))
    monkeypatch.setattr(tools, "remember_node", AsyncMock())
    monkeypatch.setattr(tools, "cutscene_inventory", AsyncMock(return_value=[
        {"asset_node_id": 100, "variant_node_id": 2, "visual_reference_id": 12}]))
    monkeypatch.setattr(evidence, "quest_visual_reference", AsyncMock(return_value=report))
    monkeypatch.setattr(evidence, "visual_text_context", AsyncMock(return_value=context))
    first = await tools.read_cutscene_context(100, limit=1)
    assert first["items"][0]["source_kind"] == "caption" and first["next_offset"] == 1
    second = await tools.read_cutscene_context(100, offset=1, limit=1)
    assert second["items"][0]["source_kind"] == "dialogue" and second["next_offset"] is None
    assert tools.visual_evidence["12"]["context_indices"] == [0, 1]
    tools.settings.tool_result_chars = 1000
    context["dialogue_lines"] = [{"node_id": i + 10, "text": "x" * 180} for i in range(8)]
    tools.visual_evidence = {}
    page = await tools.read_cutscene_context(100, limit=50)
    assert len(json.dumps(page, ensure_ascii=False)) <= 1000
    assert page["next_offset"] is not None
