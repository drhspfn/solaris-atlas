"""Tests for video_analysis role separation, dynamic limits, bypass, and admin cleanup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.agents.capabilities import (
    VIDEO_ANALYSIS_TOOLS,
    allowed,
    analysis_prompt,
    video_analysis_definitions,
)
from wuwa_story.agents.cutscene_vision import (
    VideoAnalysisResult,
    dynamic_sampling_plan,
    validate_resample_request,
    wipe_cutscene_analysis,
)
from wuwa_story.agents.settings import AgentSettings


def test_video_analysis_role_and_tools_are_separated():
    quest_prompt = analysis_prompt("quest_analysis")
    video_prompt = analysis_prompt("video_analysis")
    assert quest_prompt != video_prompt
    assert "Video Analysis" in video_prompt
    assert "identification is unavailable" in video_prompt
    assert "`unknown` and `unresolved` are not identifications" in video_prompt

    tool_names = {tool["name"] for tool in video_analysis_definitions()}
    assert tool_names == set(VIDEO_ANALYSIS_TOOLS)
    video_only_tools = {
        "resample_video_window",
        "identify_atlas_characters",
        "read_node",
        "graph_neighbors",
        "finish_video_analysis",
    }
    for name in tool_names:
        assert allowed("worker", name)
    for name in video_only_tools:
        assert not allowed("public", name)


def test_dynamic_sampling_plan_scales_with_duration():
    settings = AgentSettings(_env_file=None)
    short = dynamic_sampling_plan(30.0, settings)
    medium = dynamic_sampling_plan(180.0, settings)
    long = dynamic_sampling_plan(1800.0, settings)

    assert short["overview_interval"] <= medium["overview_interval"]
    assert medium["overview_interval"] == 2.0
    assert medium["max_overview_frames"] <= settings.vision_max_frames
    existing = AgentSettings(_env_file=None, vision_frame_interval=3)
    assert dynamic_sampling_plan(180.0, existing)["overview_interval"] == 3.0
    assert long["overview_interval"] >= medium["overview_interval"]
    assert short["soft_zoom_frames"] <= medium["soft_zoom_frames"] <= 48
    assert long["hard_zoom_frames"] == 96


def test_validate_resample_request_guides_and_allows_bypass():
    ok = validate_resample_request(
        120.0,
        start_seconds=10.0,
        end_seconds=18.0,
        interval_seconds=0.5,
        soft_max_frames=24,
    )
    assert ok["ok"] is True
    assert ok["bypassed"] is False
    assert len(ok["times"]) == 17

    soft = validate_resample_request(
        300.0,
        start_seconds=10.0,
        end_seconds=40.0,
        interval_seconds=0.5,
        soft_max_frames=24,
    )
    assert soft["ok"] is False
    assert soft["error"] == "soft_limit_exceeded"
    assert len(soft["alternatives"]) >= 2

    bypassed = validate_resample_request(
        300.0,
        start_seconds=10.0,
        end_seconds=40.0,
        interval_seconds=0.5,
        soft_max_frames=24,
        bypass_limit=True,
        bypass_justification="Character crosses frame rapidly during combat sequence.",
    )
    assert bypassed["ok"] is True
    assert bypassed["bypassed"] is True

    hard = validate_resample_request(
        600.0,
        start_seconds=0.0,
        end_seconds=300.0,
        interval_seconds=0.5,
        soft_max_frames=24,
        hard_max_frames=96,
        bypass_limit=True,
        bypass_justification="Too broad",
    )
    assert hard["ok"] is False
    assert hard["error"] == "hard_limit_exceeded"


def test_video_analysis_result_validates_event_driven_fields():
    result = VideoAnalysisResult(
        title="Somnoire Awakening",
        summary="Rover touches the Somnoire relic and triggers a dream rift.",
        events=[
            {
                "time": 1.5,
                "global_frame": 2,
                "observation": "Rover approaches the glowing Somnoire mirror.",
                "confidence": "observed",
                "actors": ["Rover"],
                "items": ["Somnoire Mirror"],
                "lore_topics": ["Somnoire"],
            }
        ],
        story_events=[
            {
                "title": "Somnoire Rift Opens",
                "summary": "Touching the relic opens a dream corridor.",
                "time_start": 1.0,
                "time_end": 5.0,
                "participants": ["Rover"],
                "topics": ["Somnoire"],
            }
        ],
        entity_links=[
            {
                "target_node_id": 42,
                "target_name": "Somnoire",
                "kind": "lore_topic",
                "relation": "mentions",
                "confidence": "dialogue_sourced",
                "evidence": "Dialogue explicitly names Somnoire.",
            }
        ],
        lore_topics=["Somnoire"],
        preview_candidates=[{"time": 1.5, "global_frame": 2, "score": 92}],
    )
    assert result.lore_topics == ["Somnoire"]
    assert result.entity_links[0].target_node_id == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_id", [55, 15536])
async def test_wipe_retains_shared_content_without_network_io(owner_id):
    scalar_values = iter([
        1,       # advisory lock
        9,       # Processor.id
        1,       # run lock
    ])
    scalars_values = iter([
        [77],    # run_ids
        [501],   # source_video_ids
        [        # FileReference rows
            SimpleNamespace(id=10, file_id=100),
            SimpleNamespace(id=11, file_id=200),
        ],
        [100],   # variant file_ids
    ])

    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=lambda *_a, **_kw: next(scalar_values)),
        scalars=AsyncMock(side_effect=lambda *_a, **_kw: next(scalars_values)),
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=2)),
        flush=AsyncMock(),
    )
    storage = SimpleNamespace(delete=AsyncMock())

    summary = await wipe_cutscene_analysis(session, storage, asset_id=owner_id, game_version="2.1")

    assert summary == {
        "asset_node_id": owner_id,
        "game_version": "2.1",
        "deleted_runs": 1,
        "deleted_references": 2,
        "deleted_files": 0,
        "deleted_s3_objects": 0,
        "retained_files": 2,
        "deleted_edges": 2,
        "deleted_lore_chunks": 2,
    }
    storage.delete.assert_not_awaited()
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    variants = [statement for statement in statements if statement.startswith("DELETE FROM storage.file_variant")]
    assert len(variants) == 1
    assert "processor_run_id" in variants[0]
    assert "child_file_id" not in variants[0]
    assert not any(statement.startswith("DELETE FROM storage.file_object") for statement in statements)
    assert not any(statement.startswith("DELETE FROM storage.file_location") for statement in statements)


@pytest.mark.asyncio
async def test_wipe_rejects_active_run_before_any_delete():
    import hashlib

    session = SimpleNamespace(scalar=AsyncMock(side_effect=[True, 9, False]),
                              scalars=AsyncMock(return_value=[77]), execute=AsyncMock())
    storage = SimpleNamespace(delete=AsyncMock())
    with pytest.raises(ValueError, match="currently running"):
        await wipe_cutscene_analysis(session, storage, asset_id=55, game_version="2.1")
    assert session.execute.await_count == 1  # source admission lock only
    key = int.from_bytes(hashlib.sha256(b"cutscene-vision-source:2.1:55").digest()[:7], "big")
    assert session.execute.await_args.args[1] == {"key": key}
    storage.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_wipe_requires_version_before_locks_or_deletion():
    session = SimpleNamespace(scalar=AsyncMock(), scalars=AsyncMock(), execute=AsyncMock())
    storage = SimpleNamespace(delete=AsyncMock())
    with pytest.raises(ValueError, match="specific asset version"):
        await wipe_cutscene_analysis(session, storage, asset_id=55)
    session.scalar.assert_not_awaited()
    session.execute.assert_not_awaited()
    storage.delete.assert_not_awaited()
