"""Character model contracts and durable enqueueing, shared with the model worker.

Workers must claim the exact metadata payload and publish references atomically
with the same nonempty metadata model_bundle_id on preview, download and optional physics.
"""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.repositories.character_models import (
    REFERENCE_TYPES,
    model_release,
    published_model_files,
)
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.ingestion.client_assets import inspect_installed_clients
from wuwa_story.ingestion.media_jobs import publish_media_job
from wuwa_story.storage.s3 import S3Storage

QUEUE = "wuwa.character-model.v1"
PROCESSOR = "character_model"


class CharacterModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_key: str = Field(pattern=r"^character:[1-9][0-9]*$")
    game_version: str | None = Field(default=None, min_length=1, max_length=64)
    force: bool = Field(default=False, strict=True)


class CharacterModelJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    job_type: Literal["media.character_model"] = "media.character_model"
    run_id: int = Field(gt=0, strict=True)
    character_key: str = Field(pattern=r"^character:[1-9][0-9]*$")
    owner_node_id: int = Field(gt=0, strict=True)
    release_id: int = Field(gt=0, strict=True)
    game_version: str = Field(min_length=1, max_length=64)
    asset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    download_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    tier: Literal["sd", "hd", "uhd"]


async def enqueue_character_model(session, request: CharacterModelRequest):
    node = await get_node(session, request.character_key, ("character",))
    if node is None:
        raise ValueError("Character not found")
    release = await model_release(session, request.game_version)
    if release is None:
        raise ValueError("Story snapshot is not imported")
    workspace = Path(os.getenv("WUWA_ASSET_WORKSPACE", "./var/client-assets")).resolve()
    installed = await asyncio.to_thread(inspect_installed_clients, workspace)
    client = next((item for item in installed if item.get("state") == "downloaded"), None)
    if client is None:
        raise ValueError("No ready installed game client")
    # The inspector sorts by asset version. Story and client versions are independent.
    job = CharacterModelJob(
        run_id=1,
        character_key=node.canonical_key,
        owner_node_id=node.id,
        release_id=release.id,
        game_version=release.game_version,
        asset_version=client["version"],
        download_id=client["download_id"],
        tier=client["tier"],
    )
    identity = job.model_dump(exclude={"run_id"})
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:id)"), {"id": int.from_bytes(digest[:7], "big")}
    )
    await session.execute(
        insert(Processor)
        .values(key=PROCESSOR, version="1")
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor_id = await session.scalar(select(Processor.id).where(Processor.key == PROCESSOR))
    run = await session.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.processor_id == processor_id,
            ProcessingRun.input_hash == digest,
        )
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    if run and (
        run.status in {"pending", "queued", "running", "waiting_dependency"}
        or run.status == "completed"
        and not request.force
    ):
        return run
    # A fresh run preserves prior results and gives every forced publication a new identity.
    run = ProcessingRun(
        processor_id=processor_id, target_node_id=node.id, input_hash=digest, status="queued"
    )
    session.add(run)
    await session.flush()
    job.run_id = run.id
    payload = job.model_dump()
    run.metadata_json = {"payload": payload, "queue": QUEUE}
    await session.commit()
    try:
        await publish_media_job(payload, f"character-model:{run.id}", QUEUE)
    except Exception:
        # An uncertain broker confirmation must not overwrite a worker's claimed state.
        await session.execute(
            update(ProcessingRun)
            .where(
                ProcessingRun.id == run.id,
                ProcessingRun.status == "queued",
            )
            .values(
                status="enqueue_failed", error="Broker did not confirm publication; retry import"
            )
        )
        await session.commit()
        raise
    return run


async def public_character_model(session, owner_node_id, release):
    result = {
        "status": "missing",
        "game_version": release.game_version if release else None,
        "preview": None,
        "download": None,
        "physics": None,
        "viewer_url": None,
    }
    if release is None:
        return result
    settings = get_settings()
    rows = await published_model_files(session, owner_node_id, release.id, settings.s3_bucket)
    # Rows are newest first. Never combine files from different publications.
    bundles = {}
    for reference_type, model_bundle_id, object_key, size_bytes, viewer_object_key in rows:
        if model_bundle_id:
            bundles.setdefault(model_bundle_id, {}).setdefault(
                REFERENCE_TYPES[reference_type], (object_key, size_bytes, viewer_object_key)
            )
    bundle = next(
        (files for files in bundles.values() if "preview" in files and "download" in files), None
    )
    if bundle is not None:
        storage = S3Storage(settings)
        result["status"] = "ready"
        for kind, (object_key, size_bytes, viewer_object_key) in bundle.items():
            result[kind] = {"url": storage.public_url(object_key), "size_bytes": size_bytes}
            if isinstance(viewer_object_key, str) and re.fullmatch(
                r"model-viewer/[0-9a-f]{64}/index\.html", viewer_object_key
            ):
                result["viewer_url"] = storage.public_url(viewer_object_key)
    return result
