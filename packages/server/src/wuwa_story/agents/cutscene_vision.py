"""Bounded visual observations: generated evidence, never game-authored dialogue."""

import hashlib
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import CutsceneDescription
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.agents.video_context import video_context_snapshot
from wuwa_story.agents.visual_enqueue_lock import lock_visual_sources
from wuwa_story.db.models.agents import AgentCall
from wuwa_story.db.models.graph import Edge
from wuwa_story.db.models.lore import LoreChunk
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.storage import FileObject, FileReference, FileVariant
from wuwa_story.story.cutscene_publications import publication_order, publication_types

VISION_VERSION = "video-analysis-v5"
MIN_VISUAL_SECONDS = 5.0


class VideoEntityLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_node_id: int | None = Field(default=None, gt=0)
    canonical_key: str | None = Field(default=None, min_length=1, max_length=256)
    target_name: str = Field(min_length=1, max_length=200)
    kind: Literal["character", "item", "location", "event", "quest", "lore_topic"]
    relation: Literal["appears_in", "uses_item", "located_at", "related_to_event", "mentions"]
    confidence: Literal["observed", "dialogue_sourced", "inferred"]
    evidence: str = Field(min_length=1, max_length=500)


class VideoStoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=600)
    time_start: float = Field(ge=0)
    time_end: float = Field(ge=0)
    participants: list[str] = Field(default_factory=list, max_length=24)
    topics: list[str] = Field(default_factory=list, max_length=24)


class VideoAnalysisEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time: float = Field(ge=0)
    global_frame: int | None = Field(default=None, ge=1)
    observation: str = Field(min_length=1, max_length=600)
    confidence: Literal["observed", "uncertain"]
    actors: list[str] = Field(default_factory=list, max_length=16)
    items: list[str] = Field(default_factory=list, max_length=16)
    lore_topics: list[str] = Field(default_factory=list, max_length=16)
    time_end: float | None = Field(default=None, ge=0)
    evidence_kind: Literal["observed", "inferred", "contextual"] | None = None
    evidence_frames: list[int] = Field(default_factory=list, max_length=24)
    context_sources: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.time_end is not None and self.time_end < self.time:
            raise ValueError("Event end cannot precede its start")
        if any(frame < 1 for frame in self.evidence_frames):
            raise ValueError("Evidence frame numbers must be positive")
        return self


class VideoPreviewCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time: float = Field(ge=0)
    global_frame: int | None = Field(default=None, ge=1)
    score: int = Field(ge=0, le=100)


class VideoAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[^\r\n]+$")
    summary: str = Field(min_length=1, max_length=1600)
    events: list[VideoAnalysisEvent] = Field(min_length=1, max_length=200)
    story_events: list[VideoStoryEvent] = Field(default_factory=list, max_length=32)
    entity_links: list[VideoEntityLink] = Field(default_factory=list, max_length=64)
    lore_topics: list[str] = Field(default_factory=list, max_length=32)
    preview_candidates: list[VideoPreviewCandidate] = Field(default_factory=list, max_length=5)

    @field_validator("title", mode="before")
    @classmethod
    def readable_video_title(cls, value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if (not value or len(value) > 120 or "\n" in value or "\r" in value
                or re.fullmatch(r"(?:cutscene\s*:?[ ]*)?[A-Za-z_]*\d[A-Za-z0-9_-]*", value, re.I)):
            return None
        return value

    @model_validator(mode="after")
    def ordered_events(self):
        if [event.time for event in self.events] != sorted(event.time for event in self.events):
            raise ValueError("Video analysis events must be chronological")
        return self


def dynamic_sampling_plan(duration: float, settings: AgentSettings) -> dict[str, float | int]:
    """Compute overview interval and zoom-window limits dynamically from video duration."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Duration must be finite and positive")
    base_interval = float(settings.vision_frame_interval)
    if duration <= 45.0:
        overview_interval = min(base_interval, 2.0)
    elif duration <= 300.0:
        overview_interval = base_interval
    else:
        overview_interval = max(base_interval, round(duration / max(24, settings.vision_max_frames // 2), 2))
    max_overview_frames = min(settings.vision_max_frames, max(12, math.ceil(duration / overview_interval) + 1))
    soft_zoom_frames = min(48, max(16, round(min(duration, 120.0) * 0.4)))
    return {
        "overview_interval": overview_interval,
        "max_overview_frames": max_overview_frames,
        "soft_zoom_frames": soft_zoom_frames,
        "hard_zoom_frames": 96,
    }


def validate_resample_request(
    duration: float,
    start_seconds: float,
    end_seconds: float,
    interval_seconds: float,
    *,
    soft_max_frames: int,
    hard_max_frames: int = 96,
    bypass_limit: bool = False,
    bypass_justification: str | None = None,
) -> dict[str, object]:
    """Validate a zoom-window resample request; guide the agent with alternatives when over soft limit."""
    if not (math.isfinite(start_seconds) and math.isfinite(end_seconds) and math.isfinite(interval_seconds)):
        raise ValueError("Sampling bounds and interval must be finite")
    if start_seconds < 0 or end_seconds <= start_seconds or start_seconds >= duration:
        raise ValueError(f"Invalid time window [{start_seconds}, {end_seconds}] for duration {duration:.2f}s")
    if not 0.1 <= interval_seconds <= 30.0:
        raise ValueError("interval_seconds must be between 0.1 and 30.0")
    clamped_end = min(duration, end_seconds)
    window = clamped_end - start_seconds
    count = max(2, math.ceil(window / interval_seconds) + 1)
    if count > hard_max_frames:
        suggested_interval = round(window / max(1, hard_max_frames - 1), 2)
        suggested_window = round(interval_seconds * (soft_max_frames - 1), 1)
        return {
            "ok": False,
            "error": "hard_limit_exceeded",
            "requested_frames": count,
            "hard_max_frames": hard_max_frames,
            "message": f"Requested {count} frames exceeds hard cap ({hard_max_frames}).",
            "alternatives": [
                f"Inspect a shorter {suggested_window}s slice at {interval_seconds}s interval first.",
                f"Or keep [{start_seconds:.1f}s, {clamped_end:.1f}s] with interval_seconds >= {suggested_interval}s.",
            ],
        }
    if count > soft_max_frames and (not bypass_limit or not (bypass_justification or "").strip()):
        suggested_interval = round(window / max(1, soft_max_frames - 1), 2)
        suggested_window = round(interval_seconds * (soft_max_frames - 1), 1)
        return {
            "ok": False,
            "error": "soft_limit_exceeded",
            "requested_frames": count,
            "soft_max_frames": soft_max_frames,
            "message": (
                f"Window [{start_seconds:.1f}s, {clamped_end:.1f}s] at {interval_seconds}s needs "
                f"{count} frames (soft limit {soft_max_frames})."
            ),
            "alternatives": [
                f"Narrow the window to ~{suggested_window}s (e.g. {start_seconds:.1f}s..{min(clamped_end, start_seconds + suggested_window):.1f}s) first, then decide if more is needed.",
                f"Or use interval_seconds >= {suggested_interval}s for a quick pass.",
                "Or re-call with bypass_limit=true and a concrete bypass_justification explaining why this full dense window is required.",
            ],
        }
    last_offset = max(0.0, clamped_end - min(0.05, window / 2))
    times = [
        round(start_seconds + (last_offset - start_seconds) * idx / (count - 1), 4)
        for idx in range(count)
    ]
    return {"ok": True, "times": times, "bypassed": bool(count > soft_max_frames and bypass_limit)}


class VisualEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time: float = Field(ge=0)
    observation: str = Field(min_length=1, max_length=600)
    confidence: Literal["observed", "uncertain"]


class PreviewCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, le=63, strict=True)
    score: int = Field(ge=0, le=100, strict=True)


class VisualBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=800)
    events: list[VisualEvent] = Field(min_length=1, max_length=64)
    preview_candidates: list[PreviewCandidate] = Field(default_factory=list, max_length=3)
    title: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[^\r\n]+$")

    @field_validator("title", mode="before")
    @classmethod
    def readable_title(cls, value):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if (not value or len(value) > 120 or "\n" in value or "\r" in value
                or re.fullmatch(r"(?:cutscene\s*:?[ ]*)?[A-Za-z_]*\d[A-Za-z0-9_-]*", value, re.I)):
            return None
        return value

    @model_validator(mode="after")
    def ordered(self):
        if [event.time for event in self.events] != sorted(event.time for event in self.events):
            raise ValueError("Visual events must be chronological")
        return self


def sample_times(duration: float, interval: float, max_frames: int) -> list[float]:
    if (
        not math.isfinite(duration)
        or not math.isfinite(interval)
        or duration <= 0
        or interval <= 0
        or max_frames < 2
    ):
        raise ValueError("Invalid bounded sampling plan")
    # Preserve the ending even for very long videos. Coverage is explicitly sampled.
    count = min(max_frames, max(2, math.ceil(duration / interval) + 1))
    last = max(0, duration - min(0.05, duration / 2))
    return [round(last * index / (count - 1), 4) for index in range(count)]


def validate_batch(raw: str, times: list[float]) -> dict:
    if not times:
        raise ValueError("Empty visual batch")
    batch = VisualBatch.model_validate_json(raw)
    if any(candidate.index >= len(times) for candidate in batch.preview_candidates):
        raise ValueError("Preview index is outside the sampled batch")
    if any(not times[0] - 0.05 <= event.time <= times[-1] + 0.05 for event in batch.events):
        raise ValueError("Observation timestamp is outside the sampled batch")
    if len(batch.events) != len(times) or any(
        abs(event.time - time) > 0.05 for event, time in zip(batch.events, times, strict=True)
    ):
        raise ValueError(
            "Return one timestamped observation for every sampled frame, including the ending"
        )
    return batch.model_dump()


def validate_description(
    description: CutsceneDescription, metadata: dict, read_indices: list[int]
) -> None:
    if set(read_indices) != set(range(len(metadata["events"]))):
        raise ValueError("Read every visual page before describing the whole cutscene")
    indices = [
        *description.observation_indices,
        *[i for chapter in description.chapters for i in chapter.observation_indices],
    ]
    if any(i not in read_indices for i in indices):
        raise ValueError("Description references unread visual observations")
    previous_end = 0
    for chapter in description.chapters:
        if (
            chapter.start < previous_end
            or chapter.end <= chapter.start
            or chapter.end > metadata["duration"]
        ):
            raise ValueError("Cutscene chapters must have ordered, nonoverlapping valid times")
        previous_end = chapter.end
        if any(
            not chapter.start <= metadata["events"][i]["time"] <= chapter.end
            for i in chapter.observation_indices
        ):
            raise ValueError("Chapter observations must fall inside its time range")


async def enqueue_visual_job(
    session: AsyncSession, reference: FileReference, settings: AgentSettings, *,
    context: str | None = None,
):
    if not settings.vision_enabled:
        return None
    version = (reference.metadata_json or {}).get("asset_version")
    if reference.owner_node_id and version:
        from wuwa_story.agents.visual_enqueue_lock import lock_visual_sources

        await lock_visual_sources(session, [reference.owner_node_id], version)
        logical = await session.scalar(select(ProcessingRun.id).join(Processor).where(
            Processor.key == "cutscene_vision",
            ProcessingRun.metadata_json["asset_version"].astext == version,
            or_(ProcessingRun.metadata_json["selected_asset_node_ids"].contains([reference.owner_node_id]),
                ProcessingRun.metadata_json["ignored_alternative_asset_node_ids"].contains([reference.owner_node_id])),
        ).limit(1))
        if logical:
            raise ValueError("A logical scene analysis already exists for this video; reconcile it first")
    file = await session.get(FileObject, reference.file_id)
    if file is None or file.sha256 is None:
        raise ValueError("Visual analysis needs a content-addressed published video")
    text_context = await video_context_snapshot(session, reference, context=context)
    config = settings.public_config()
    report = None
    if getattr(reference, "reference_type", None) in publication_types("cutscene_video") and reference.owner_node_id:
        report = await visual_reference(session, reference.owner_node_id, reference.metadata_json.get("asset_version"))
        if report and (report.file_id != file.id or not report.metadata_json.get("events")
                       or report.metadata_json.get("title")):
            report = None
    identity = hashlib.sha256(
        json.dumps(
            {
                "source": file.sha256.hex(),
                "owner": reference.owner_node_id,
                "version": reference.metadata_json.get("asset_version"),
                "protocol": VISION_VERSION,
                "temporal_review_version": 1,
                "config": config,
                "text_context": text_context,
                "title_source_report": report.id if report else None,
            },
            sort_keys=True,
        ).encode()
    ).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(identity[:7], "big")}
    )
    await session.execute(
        insert(Processor)
        .values(key="cutscene_vision", version=VISION_VERSION)
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor = await session.scalar(select(Processor.id).where(Processor.key == "cutscene_vision"))
    existing = await session.scalar(
        select(ProcessingRun).where(
            ProcessingRun.processor_id == processor, ProcessingRun.input_hash == identity
        )
    )
    if existing:
        return existing
    run = ProcessingRun(
        processor_id=processor,
        target_node_id=reference.owner_node_id,
        prompt_version=VISION_VERSION,
        input_hash=identity,
        status="pending",
        metadata_json={
            "reference_id": reference.id,
            "file_id": file.id,
            "source_sha256": file.sha256.hex(),
            "asset_version": reference.metadata_json.get("asset_version"),
            "settings": config,
            "text_context": text_context,
            "temporal_review_version": 1,
            **({"title_only_report_id": report.id} if report else {}),
        },
        raw_output={"batches": [], "step": 0},
    )
    session.add(run)
    await session.flush()
    return run


async def visual_reference(session: AsyncSession, asset_id: int, version: str | None = None):
    # Only a complete report matching the currently published bytes can be consumed.
    video = await session.scalar(
        select(FileReference)
        .where(
            FileReference.owner_node_id == asset_id,
            FileReference.reference_type.in_(publication_types("cutscene_video")),
            *([FileReference.metadata_json["asset_version"].astext == version] if version else []),
        )
        .order_by(publication_order(FileReference.reference_type), FileReference.id.desc())
        .limit(1)
    )
    if video is None:
        return None
    file = await session.get(FileObject, video.file_id)
    if file is None or file.sha256 is None:
        return None
    return await session.scalar(
        select(FileReference)
        .where(
            FileReference.owner_node_id == asset_id,
            FileReference.reference_type == "cutscene_visual_description",
            FileReference.metadata_json["source_sha256"].astext == file.sha256.hex(),
            FileReference.metadata_json["asset_version"].astext
            == video.metadata_json["asset_version"],
            FileReference.metadata_json["complete"].as_boolean().is_(True),
        )
        .order_by(FileReference.id.desc())
        .limit(1)
    )


async def resume_visual_job(session: AsyncSession, run_id: int, output_tokens: int | None = None):
    locked = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": -run_id})
    if not locked:
        raise ValueError("Visual job is currently running")
    run = await session.get(ProcessingRun, run_id)
    processor = await session.get(Processor, run.processor_id) if run else None
    if (
        not processor
        or processor.key != "cutscene_vision"
        or run.status
        not in (
            "paused_budget",
            "paused_output",
            "paused_validation",
            "paused_provider",
            "paused_config",
            "paused_input",
        )
    ):
        raise ValueError(
            "Select a paused visual job with known billing; uncertain calls cannot be retried"
        )
    if await session.scalar(
        select(AgentCall.id)
        .where(AgentCall.run_id == run_id, AgentCall.status.not_in(["completed"]))
        .limit(1)
    ):
        raise ValueError("Reconcile uncertain billing before resuming")
    config = dict(run.metadata_json["settings"])
    if run.status == "paused_output" and (
        output_tokens is None or output_tokens <= config["vision_output_tokens"]
    ):
        raise ValueError("Increase output tokens to resume a truncated single frame")
    if output_tokens is not None:
        config["vision_output_tokens"] = output_tokens
    AgentSettings(_env_file=None, **config)
    run.metadata_json = {**run.metadata_json, "settings": config, "last_dispatched": 0}
    run.status, run.error = "pending", None
    return run


_ANALYSIS_REF_TYPES = (
    "cutscene_visual_description",
    "cutscene_visual_title",
    "cutscene_preview",
    "video_frame_atlas",
)


async def wipe_cutscene_analysis(
    session: AsyncSession,
    storage: object,
    asset_id: int,
    game_version: str | None = None,
) -> dict[str, int | str | None]:
    """Remove analysis records while retaining content-addressed storage objects."""
    if not game_version:
        raise ValueError("Select a specific asset version before deleting analysis")
    await lock_visual_sources(session, [asset_id], game_version)
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": -asset_id - 1_000_000}
    )
    if not locked:
        raise ValueError("Cutscene analysis cleanup is already in progress")

    proc_id = await session.scalar(select(Processor.id).where(Processor.key == "cutscene_vision"))
    run_ids: list[int] = []
    if proc_id is not None:
        run_stmt = select(ProcessingRun.id).where(
            ProcessingRun.processor_id == proc_id,
            ProcessingRun.target_node_id == asset_id,
        )
        if game_version:
            run_stmt = run_stmt.where(
                ProcessingRun.metadata_json["asset_version"].astext == game_version
            )
        run_ids = sorted(await session.scalars(run_stmt))
        for run_id in run_ids:
            if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": -run_id}):
                raise ValueError("Cutscene analysis is currently running; retry after it finishes")

    ref_stmt = select(FileReference).where(
        FileReference.owner_node_id == asset_id,
        FileReference.reference_type.in_(_ANALYSIS_REF_TYPES),
    )
    if game_version:
        source_video_ids = list(
            await session.scalars(
                select(FileReference.file_id).where(
                    FileReference.owner_node_id == asset_id,
                    FileReference.reference_type.in_(publication_types("cutscene_video")),
                    FileReference.metadata_json["asset_version"].astext == game_version,
                )
            )
        )
        version_clauses = [
            FileReference.metadata_json["asset_version"].astext == game_version,
        ]
        if source_video_ids:
            version_clauses.append(
                FileReference.metadata_json["source_file_id"].as_integer().in_(source_video_ids)
            )
        ref_stmt = ref_stmt.where(or_(*version_clauses))
    refs = list(await session.scalars(ref_stmt))
    ref_ids = [r.id for r in refs]
    candidate_file_ids = {r.file_id for r in refs if r.file_id is not None}

    deleted_edges = 0
    if run_ids:
        variant_file_ids = list(
            await session.scalars(
                select(FileVariant.child_file_id).where(
                    FileVariant.processor_run_id.in_(run_ids),
                    FileVariant.variant_type.in_(("cutscene_preview", "video_frame_atlas")),
                )
            )
        )
        candidate_file_ids.update(variant_file_ids)
        await session.execute(delete(FileVariant).where(
            FileVariant.processor_run_id.in_(run_ids),
            FileVariant.variant_type.in_(("cutscene_preview", "video_frame_atlas"))))
        edge_res = await session.execute(delete(Edge).where(
            Edge.from_node_id == asset_id, Edge.processor_run_id.in_(run_ids),
            Edge.layer == "semantic", Edge.basis == "inference"))
        deleted_edges = int(edge_res.rowcount or 0)
        await session.execute(delete(AgentCall).where(AgentCall.run_id.in_(run_ids)))
        await session.execute(delete(ProcessingRun).where(ProcessingRun.id.in_(run_ids)))

    if ref_ids:
        await session.execute(delete(FileReference).where(FileReference.id.in_(ref_ids)))

    lore_stmt = delete(LoreChunk).where(
        LoreChunk.source_type == "cutscene_vision",
        LoreChunk.source_id == str(asset_id),
    )
    if game_version:
        lore_stmt = lore_stmt.where(LoreChunk.game_version == str(game_version)[:20])
    lore_res = await session.execute(lore_stmt)
    await session.flush()

    # There is no content-addressed object GC; retained files remain until a
    # separately coordinated cleanup can safely handle concurrent registration.
    return {
        "asset_node_id": asset_id,
        "game_version": game_version,
        "deleted_runs": len(run_ids),
        "deleted_references": len(ref_ids),
        "deleted_files": 0,
        "deleted_s3_objects": 0,
        "retained_files": len(candidate_file_ids),
        "deleted_edges": deleted_edges,
        "deleted_lore_chunks": int(lore_res.rowcount or 0),
    }
