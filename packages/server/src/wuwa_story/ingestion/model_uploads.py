"""Admin-only chunked model uploads; no server-side game extraction is required."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.repositories.character_models import model_release
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.ingestion.character_models import (
    PROCESSOR,
    QUEUE,
    CharacterModelRequest,
    public_character_model,
)
from wuwa_story.ingestion.media_jobs import publish_media_job
from wuwa_story.ingestion.model_archives import CHUNK_BYTES, MAX_ARCHIVE_BYTES
from wuwa_story.storage.s3 import S3Storage


class ModelUploadRequest(CharacterModelRequest):
    archive_bytes: int = Field(gt=0, le=MAX_ARCHIVE_BYTES, strict=True)
    upload_token: str = Field(pattern=r"^[0-9a-f]{32}$")


class ModelUploadJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    job_type: Literal["media.character_model_upload"] = "media.character_model_upload"
    run_id: int = Field(gt=0, strict=True)
    character_key: str = Field(pattern=r"^character:[1-9][0-9]*$")
    owner_node_id: int = Field(gt=0, strict=True)
    release_id: int = Field(gt=0, strict=True)
    game_version: str = Field(min_length=1, max_length=64)
    archive_bytes: int = Field(gt=0, le=MAX_ARCHIVE_BYTES, strict=True)
    storage_token: str = Field(pattern=r"^[0-9a-f]{32}$")


def chunk_count(job):
    return (job.archive_bytes + CHUNK_BYTES - 1) // CHUNK_BYTES


def chunk_key(job, index):
    if type(index) is not int or not 0 <= index < chunk_count(job):
        raise ValueError("Invalid model upload chunk")
    return f"model-uploads/{job.storage_token}/{index:04d}"


async def start_upload(session, request):
    node = await get_node(session, request.character_key, ("character",))
    release = await model_release(session, request.game_version)
    if node is None or release is None:
        raise ValueError("Character or story snapshot is missing")
    identity = hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True).encode()).digest()
    await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": int.from_bytes(identity[:7], "big")})
    await session.execute(insert(Processor).values(key=PROCESSOR, version="1").on_conflict_do_nothing(index_elements=[Processor.key]))
    processor_id = await session.scalar(select(Processor.id).where(Processor.key == PROCESSOR))
    existing = await session.scalar(select(ProcessingRun).where(
        ProcessingRun.processor_id == processor_id, ProcessingRun.input_hash == identity,
    ).order_by(ProcessingRun.id.desc()).limit(1))
    if existing:
        return existing
    if not request.force and (await public_character_model(session, node.id, release))["status"] == "ready":
        raise ValueError("A model is already published; enable Rebuild existing model to replace it")
    run = ProcessingRun(processor_id=processor_id, target_node_id=node.id, input_hash=identity, status="uploading")
    session.add(run)
    await session.flush()
    job = ModelUploadJob(run_id=run.id, character_key=node.canonical_key, owner_node_id=node.id,
                         release_id=release.id, game_version=release.game_version,
                         archive_bytes=request.archive_bytes, storage_token=uuid.uuid4().hex)
    run.metadata_json = {"payload": job.model_dump(), "queue": QUEUE, "uploaded_parts": [],
                         "force": request.force}
    await session.commit()
    return run


async def upload_run(session, run_id, *, lock=False):
    query = select(ProcessingRun).where(ProcessingRun.id == run_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = await session.scalar(query)
    if run is None:
        raise ValueError("Model upload not found")
    job = ModelUploadJob.model_validate(run.metadata_json.get("payload"))
    return run, job


async def store_chunk(session, run_id, index, body):
    run, job = await upload_run(session, run_id)
    key = chunk_key(job, index)
    if run.status != "uploading":
        raise ValueError("Model upload is already closed")
    expected = min(CHUNK_BYTES, job.archive_bytes - index * CHUNK_BYTES)
    await session.commit()
    data = bytearray()
    async for block in body:
        if len(data) + len(block) > expected:
            raise ValueError("Model upload chunk exceeds declared size")
        data.extend(block)
    if len(data) != expected:
        raise ValueError("Incomplete model upload chunk")
    storage = S3Storage(get_settings())
    checksum = hashlib.sha256(data).hexdigest()
    try:
        await asyncio.to_thread(storage.client.put_object, Bucket=storage.bucket, Key=key,
            Body=bytes(data), ContentType="application/octet-stream", IfNoneMatch="*",
            Metadata={"sha256": checksum})
    except ClientError as error:
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
        head = await asyncio.to_thread(storage.client.head_object, Bucket=storage.bucket, Key=key)
        if head.get("Metadata", {}).get("sha256") != checksum:
            raise ValueError("This upload chunk already contains different data; start a new upload") from error
    run, job = await upload_run(session, run_id, lock=True)
    if run.status != "uploading":
        raise ValueError("Model upload closed while receiving data")
    parts = sorted(set(run.metadata_json.get("uploaded_parts", [])) | {index})
    run.metadata_json = {**run.metadata_json, "uploaded_parts": parts}
    run.raw_output = {"stage": "upload", "batch": len(parts), "batches": chunk_count(job)}
    await session.commit()


async def finish_upload(session, run_id):
    run, job = await upload_run(session, run_id, lock=True)
    if run.status in {"completed", "running", "queued"}:
        return run
    if run.status not in {"uploading", "enqueue_failed", "failed"}:
        raise ValueError("Model upload is closed")
    if set(run.metadata_json.get("uploaded_parts", [])) != set(range(chunk_count(job))):
        raise ValueError("Model archive upload is incomplete")
    run.status = "queued"
    run.error = None
    await session.commit()
    try:
        await publish_media_job(job.model_dump(), f"character-model:{job.run_id}", QUEUE)
    except Exception:
        await session.execute(update(ProcessingRun).where(ProcessingRun.id == run_id,
            ProcessingRun.status == "queued").values(status="enqueue_failed", error="Broker did not confirm model upload; retry"))
        await session.commit()
        raise
    return run


async def expire_uploads(session):
    """Collect abandoned browser uploads when the next upload starts."""
    runs = list(await session.scalars(select(ProcessingRun).where(
        ProcessingRun.status == "uploading", ProcessingRun.started_at < datetime.now(UTC) - timedelta(hours=24),
        ProcessingRun.metadata_json["payload"]["job_type"].astext == "media.character_model_upload",
    ).with_for_update(skip_locked=True).limit(20)))
    jobs = [(ModelUploadJob.model_validate(run.metadata_json["payload"]), run.metadata_json.get("uploaded_parts", [])) for run in runs]
    for run in runs:
        run.status, run.error = "cancelled", "Abandoned model upload expired after 24 hours"
    await session.commit()
    storage = S3Storage(get_settings())
    for job, parts in jobs:
        if parts:
            await asyncio.to_thread(storage.client.delete_objects, Bucket=storage.bucket,
                Delete={"Objects": [{"Key": chunk_key(job, index)} for index in parts], "Quiet": True})
