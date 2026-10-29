import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import wuwa_story.agents.cutscene_vision as vision
from wuwa_story.agents.contracts import CutsceneDescription, QuestAssessment
from wuwa_story.agents.cutscene_vision import (
    enqueue_visual_job,
    resume_visual_job,
    sample_times,
    validate_batch,
    validate_description,
    visual_reference,
)
from wuwa_story.agents.lore import assessment_policy, primary_quest_role
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.storage import FileObject, FileReference, FileType


def test_sampling_includes_ending_and_bounds_long_videos():
    times = sample_times(3600, 3, 600)
    assert len(times) == 600
    assert times[0] == 0 and times[-1] == 3599.95
    assert times == sorted(set(times))
    assert sample_times(0.02, 3, 600) == [0, 0.01]
    for duration, interval in [(float("nan"), 3), (10, float("inf")), (0, 3)]:
        with pytest.raises(ValueError):
            sample_times(duration, interval, 600)


def test_batch_rejects_truncation_wrong_time_and_out_of_order():
    def event(time):
        return {"time": time, "observation": "A person turns around", "confidence": "observed"}

    def encode(events):
        return json.dumps({"summary": "A turn", "events": events})

    assert validate_batch(encode([event(1), event(2)]), [1, 2])["events"][1]["time"] == 2
    for raw in ['{"summary":', encode([event(3)]), encode([event(2), event(1)]), encode([])]:
        with pytest.raises(ValueError):
            validate_batch(raw, [1, 2])
    with pytest.raises(ValueError, match="every sampled frame"):
        validate_batch(encode([event(1)]), [1, 2])


def test_preview_candidates_must_reference_real_sampled_frames():
    events = [{"time": t, "observation": "A scene",
               "confidence": "observed"} for t in (1, 2)]
    raw = {"summary": "A scene", "events": events,
           "preview_candidates": [{"index": 1, "score": 90}, {"index": 0, "score": 80}]}
    assert len(validate_batch(json.dumps(raw), [1, 2])[
               "preview_candidates"]) == 2
    raw["preview_candidates"] = []
    assert validate_batch(json.dumps(raw), [1, 2])["preview_candidates"] == []
    raw["preview_candidates"] = [{"index": 2, "score": 90}]
    with pytest.raises(ValueError, match="outside"):
        validate_batch(json.dumps(raw), [1, 2])


def test_visual_title_is_optional_but_must_be_readable():
    event = {"time": 1, "observation": "A scene", "confidence": "observed"}
    raw = {"summary": "A scene", "events": [event], "title": "  A Last Stand  "}
    assert validate_batch(json.dumps(raw), [1])["title"] == "A Last Stand"
    for title in ("C3jiyanfight", "M0311", " \n "):
        raw["title"] = title
        with pytest.raises(ValueError):
            validate_batch(json.dumps(raw), [1])


@pytest.mark.asyncio
async def test_enqueue_reuses_existing_visual_report_for_title(monkeypatch):
    monkeypatch.setattr(vision, "video_context_snapshot", AsyncMock(return_value={}))
    report = SimpleNamespace(id=25, file_id=2, metadata_json={"events": [{"time": 1}]})
    monkeypatch.setattr(vision, "visual_reference", AsyncMock(return_value=report))
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=2, sha256=b"source")),
                         execute=AsyncMock(), scalar=AsyncMock(side_effect=[3, None]),
                         add=Mock(), flush=AsyncMock())
    reference = SimpleNamespace(id=1, file_id=2, owner_node_id=5, reference_type="cutscene_video",
                                metadata_json={"asset_version": "3.7.0"})
    run = await enqueue_visual_job(db, reference, AgentSettings(_env_file=None, vision_enabled=True))
    assert run.metadata_json["title_only_report_id"] == 25


def description(**changes):
    return CutsceneDescription.model_validate(
        {
            "asset_node_id": 5,
            "visual_reference_id": 7,
            "title": "Arrival",
            "text": "A person arrives",
            "observation_indices": [0, 1],
            "chapters": [
                {
                    "start": 0,
                    "end": 10,
                    "title": "Arrival",
                    "text": "The arrival",
                    "observation_indices": [0, 1],
                }
            ],
            **changes,
        }
    )


def test_description_needs_complete_read_coverage_and_correct_chapter_times():
    report = {"duration": 10, "events": [{"time": 1}, {"time": 8}]}
    validate_description(description(), report, [0, 1])
    with pytest.raises(ValueError, match="every visual page"):
        validate_description(description(), report, [0])
    with pytest.raises(ValueError, match="unread"):
        validate_description(description(observation_indices=[-1]), report, [0, 1])
    invalid = description()
    invalid.chapters[0].end = 5
    with pytest.raises(ValueError, match="inside"):
        validate_description(invalid, report, [0, 1])
    invalid.chapters[0].end = 11
    with pytest.raises(ValueError, match="valid times"):
        validate_description(invalid, report, [0, 1])


def test_main_quest_cannot_be_downgraded_to_region_lore():
    assessment = QuestAssessment.model_validate(
        {
            "narrative_weight": "region_lore",
            "secondary_functions": ["Sentinel_arc"],
            "hook_priority": "medium",
            "reason": "Regional system",
            "citations": [{"node_id": 1, "quote": "The Sentinel"}],
            "signals": [
                {
                    "kind": "regional_system",
                    "explanation": "The regional protection system",
                    "citations": [{"node_id": 1, "quote": "The Sentinel"}],
                }
            ],
        }
    )
    main = primary_quest_role(assessment, "1")
    assert main.narrative_weight == "main_plot"
    assert main.secondary_functions == ["region_lore", "Sentinel_arc"]
    assert assessment_policy(main)["depth"] == "full"
    assert primary_quest_role(assessment, None).narrative_weight == "region_lore"
    main.signals = []
    assert assessment_policy(main, authored_main=True)["depth"] == "full"
    with pytest.raises(ValueError, match="cited"):
        assessment_policy(main)  # A model-supplied label alone is insufficient.


@pytest.mark.asyncio
async def test_disabled_vision_does_not_queue_or_read_storage():
    db = SimpleNamespace(get=AsyncMock())
    assert (
        await enqueue_visual_job(
            db, SimpleNamespace(), AgentSettings(_env_file=None, vision_enabled=False)
        )
        is None
    )
    db.get.assert_not_called()


@pytest.mark.asyncio
async def test_job_identity_pins_source_text_and_supports_generic_video(monkeypatch):
    snapshots = [{"basis": "caller", "context": text} for text in ("First transcript", "Corrected transcript")]
    context = AsyncMock(side_effect=snapshots)
    monkeypatch.setattr(vision, "video_context_snapshot", context)
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=2, sha256=b"source")),
        execute=AsyncMock(), scalar=AsyncMock(side_effect=[3, None, 3, None]),
        add=Mock(), flush=AsyncMock())
    reference = SimpleNamespace(id=1, file_id=2, owner_node_id=5, metadata_json={})
    settings = AgentSettings(_env_file=None, vision_enabled=True)
    first = await enqueue_visual_job(db, reference, settings, context="First transcript")
    second = await enqueue_visual_job(db, reference, settings, context="Corrected transcript")
    assert first.input_hash != second.input_hash
    assert first.metadata_json["text_context"] == snapshots[0]
    assert second.metadata_json["text_context"] == snapshots[1]
    assert first.metadata_json["asset_version"] is None
    assert first.prompt_version == vision.VISION_VERSION


@pytest.mark.asyncio
async def test_report_selection_checks_exact_video_hash_and_build():
    report = SimpleNamespace(id=9)
    db = SimpleNamespace(
        scalar=AsyncMock(
            side_effect=[
                SimpleNamespace(file_id=2, metadata_json={"asset_version": "3.7.0"}),
                report,
            ]
        ),
        get=AsyncMock(return_value=SimpleNamespace(sha256=b"ab")),
    )
    assert await visual_reference(db, 5) is report
    query = db.scalar.call_args.args[0].compile().params
    assert b"ab".hex() in query.values() and "3.7.0" in query.values()


@pytest.mark.asyncio
async def test_uncertain_visual_job_cannot_resume_or_release_reservation():
    run = SimpleNamespace(processor_id=1, status="paused_uncertain")
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=True),
        get=AsyncMock(side_effect=[run, SimpleNamespace(key="cutscene_vision")]),
    )
    with pytest.raises(ValueError, match="uncertain"):
        await resume_visual_job(db, 1)


@pytest.mark.asyncio
async def test_single_frame_truncation_needs_explicit_larger_output_allowance():
    settings = AgentSettings(_env_file=None)
    run = SimpleNamespace(
        processor_id=1, status="paused_output", metadata_json={"settings": settings.public_config()}
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[True, None]),
        get=AsyncMock(side_effect=[run, SimpleNamespace(key="cutscene_vision")]),
    )
    with pytest.raises(ValueError, match="Increase output"):
        await resume_visual_job(db, 1, settings.vision_output_tokens)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Isolated PostgreSQL required")
async def test_postgres_job_dedup_and_report_content_identity():
    engine = create_async_engine(os.environ["WUWA_TEST_DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(connection, expire_on_commit=False) as db:
                suffix = uuid4().hex
                node = Node(
                    type_id=await db.scalar(
                        select(NodeType.id).where(NodeType.key == "asset_reference")
                    ),
                    canonical_key="vision-test:" + suffix,
                )
                db.add(node)
                await db.flush()
                file = FileObject(
                    file_type_id=await db.scalar(select(FileType.id).limit(1)),
                    sha256=hashlib.sha256(suffix.encode()).digest(),
                )
                db.add(file)
                await db.flush()
                reference = FileReference(
                    owner_node_id=node.id,
                    file_id=file.id,
                    reference_type="cutscene_video",
                    metadata_json={"asset_version": "3.7.0"},
                )
                db.add(reference)
                await db.flush()
                settings = AgentSettings(_env_file=None, vision_enabled=True)
                first = await enqueue_visual_job(db, reference, settings)
                assert (await enqueue_visual_job(db, reference, settings)).id == first.id
                assert await visual_reference(db, node.id) is None
                report = FileReference(
                    owner_node_id=node.id,
                    file_id=file.id,
                    reference_type="cutscene_visual_description",
                    metadata_json={
                        "asset_version": "3.7.0",
                        "complete": True,
                        "source_sha256": file.sha256.hex(),
                    },
                )
                db.add(report)
                await db.flush()
                assert (await visual_reference(db, node.id)).id == report.id
                report.metadata_json = {**report.metadata_json, "source_sha256": "obsolete"}
                await db.flush()
                assert await visual_reference(db, node.id) is None
            await transaction.rollback()
    finally:
        await engine.dispose()
