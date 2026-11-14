"""Admin-only model uploads; linked components also verify authored client metadata."""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal

from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, cast, or_, select, text, update
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
    character_key: str = Field(pattern=r"^character:(?:npc:)?[1-9][0-9]*$")
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


_HEX_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_S3_DELETE_BATCH = 1000


async def delete_storage_keys(storage, keys: Iterable[str]) -> int:
    """Delete storage keys in batches using S3 delete_objects when available."""
    unique = list(dict.fromkeys(key for key in keys if key))
    if not unique:
        return 0
    client = getattr(storage, "client", None)
    if client is None:
        for key in unique:
            await storage.delete(key)
        return len(unique)
    for start in range(0, len(unique), _S3_DELETE_BATCH):
        batch = unique[start:start + _S3_DELETE_BATCH]
        await asyncio.to_thread(
            client.delete_objects,
            Bucket=storage.bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )
    return len(unique)


async def cleanup_upload_chunks(storage, job: ModelUploadJob, parts: Iterable[int] | None = None) -> int:
    """Remove staged upload chunks for *job* from object storage."""
    total = chunk_count(job)
    indices = range(total) if parts is None else (i for i in parts if type(i) is int and 0 <= i < total)
    return await delete_storage_keys(storage, (chunk_key(job, index) for index in indices))


def _parse_upload_job(run: ProcessingRun) -> ModelUploadJob | None:
    payload = (run.metadata_json or {}).get("payload")
    if not isinstance(payload, dict):
        return None
    try:
        return ModelUploadJob.model_validate(payload)
    except Exception:
        return None


async def _find_orphan_upload_keys(storage, active_tokens: set[str], cutoff: datetime) -> list[str]:
    client = getattr(storage, "client", None)
    if client is None:
        return []

    def _scan() -> list[str]:
        paginator = client.get_paginator("list_objects_v2")
        orphans: list[str] = []
        for page in paginator.paginate(Bucket=storage.bucket, Prefix="model-uploads/"):
            for obj in page.get("Contents") or ():
                key = obj.get("Key") or ""
                modified = obj.get("LastModified")
                if modified is None:
                    continue
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=UTC)
                if modified >= cutoff:
                    continue
                segments = key.split("/", 2)
                token = segments[1] if len(segments) >= 2 else ""
                if not _HEX_TOKEN.fullmatch(token) or token not in active_tokens:
                    orphans.append(key)
        return orphans

    return await asyncio.to_thread(_scan)


async def cleanup_model_uploads(
    session,
    storage=None,
    *,
    max_runs: int = 50,
    orphan_ttl_hours: float = 24.0,
    scan_orphans: bool = True,
) -> dict[str, int]:
    """Expire abandoned uploads, clean terminal upload chunks, and sweep orphan R2 objects."""
    storage = storage or S3Storage(get_settings())
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=orphan_ttl_hours)

    abandoned = list(await session.scalars(
        select(ProcessingRun).where(
            ProcessingRun.status.in_(("uploading", "enqueue_failed")),
            ProcessingRun.started_at < cutoff,
            ProcessingRun.metadata_json["payload"]["job_type"].astext == "media.character_model_upload",
        ).with_for_update(skip_locked=True).limit(max_runs)
    ))
    pending_deletes: list[tuple[ModelUploadJob, Iterable[int] | None]] = []
    for run in abandoned:
        job = _parse_upload_job(run)
        was_uploading = run.status == "uploading"
        run.status = "cancelled"
        run.error = f"Abandoned model upload expired after {int(orphan_ttl_hours)} hours"
        run.finished_at = now
        if job is not None:
            parts = run.metadata_json.get("uploaded_parts", []) if was_uploading else None
            pending_deletes.append((job, parts))
        run.metadata_json = {**(run.metadata_json or {}), "chunks_cleaned": True}

    terminal = list(await session.scalars(
        select(ProcessingRun).where(
            ProcessingRun.status.in_(("completed", "failed", "cancelled")),
            ProcessingRun.metadata_json["payload"]["job_type"].astext == "media.character_model_upload",
            or_(
                ProcessingRun.metadata_json["chunks_cleaned"].is_(None),
                cast(ProcessingRun.metadata_json["chunks_cleaned"].astext, Boolean).is_(False),
            ),
        ).with_for_update(skip_locked=True).limit(max_runs)
    ))
    for run in terminal:
        job = _parse_upload_job(run)
        if job is not None:
            pending_deletes.append((job, None))
        run.metadata_json = {**(run.metadata_json or {}), "chunks_cleaned": True}

    await session.commit()

    deleted_keys = 0
    for job, parts in pending_deletes:
        deleted_keys += await cleanup_upload_chunks(storage, job, parts)

    orphan_keys: list[str] = []
    if scan_orphans:
        active_runs = list(await session.scalars(
            select(ProcessingRun.metadata_json).where(
                ProcessingRun.status.in_(("uploading", "enqueue_failed", "queued", "running", "waiting_dependency")),
                ProcessingRun.metadata_json["payload"]["job_type"].astext == "media.character_model_upload",
            )
        ))
        active_tokens = {
            meta["payload"]["storage_token"]
            for meta in active_runs
            if isinstance(meta, dict) and isinstance(meta.get("payload"), dict)
            and isinstance(meta["payload"].get("storage_token"), str)
        }
        orphan_keys = await _find_orphan_upload_keys(storage, active_tokens, cutoff)
        deleted_keys += await delete_storage_keys(storage, orphan_keys)

    return {
        "expired": len(abandoned),
        "terminal_cleaned": len(terminal),
        "orphan_objects": len(orphan_keys),
        "deleted_keys": deleted_keys,
    }


async def expire_uploads(session):
    """Collect abandoned browser uploads and uncleaned terminal chunks when the next upload starts."""
    return await cleanup_model_uploads(session, max_runs=20, scan_orphans=False)
