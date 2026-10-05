"""Bounded visual observations: generated evidence, never game-authored dialogue."""

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import CutsceneDescription
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.storage import FileObject, FileReference

VISION_VERSION = "cutscene-visual-v1"


class VisualEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    time: float = Field(ge=0)
    observation: str = Field(min_length=1, max_length=600)
    confidence: Literal["observed", "uncertain"]


class VisualBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=800)
    events: list[VisualEvent] = Field(min_length=1, max_length=12)

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
    if any(not times[0] - 0.05 <= event.time <= times[-1] + 0.05 for event in batch.events):
        raise ValueError("Observation timestamp is outside the sampled batch")
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
    session: AsyncSession, reference: FileReference, settings: AgentSettings
):
    if not settings.vision_enabled:
        return None
    file = await session.get(FileObject, reference.file_id)
    if file is None or file.sha256 is None:
        raise ValueError("Visual analysis needs a content-addressed published video")
    config = settings.public_config()
    identity = hashlib.sha256(
        json.dumps(
            {
                "source": file.sha256.hex(),
                "owner": reference.owner_node_id,
                "version": reference.metadata_json["asset_version"],
                "protocol": VISION_VERSION,
                "config": config,
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
            "asset_version": reference.metadata_json["asset_version"],
            "settings": config,
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
            FileReference.reference_type == "cutscene_video",
            *([FileReference.metadata_json["asset_version"].astext == version] if version else []),
        )
        .order_by(FileReference.id.desc())
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
