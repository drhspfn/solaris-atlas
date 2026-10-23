"""Idempotent official client downloads shared by admin actions and automatic checks."""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.ingestion.client_assets import discover_plan
from wuwa_story.ingestion.media_jobs import publish_media_job


async def enqueue_client_plan(session, plan: dict) -> dict[str, Any]:
    identity = hashlib.sha256(f"asset_download:{plan['id']}".encode()).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": int.from_bytes(identity[:7], "big")},
    )
    await session.execute(
        insert(Processor)
        .values(
            key="asset_download",
            version="1",
            description="Download official game client archives",
        )
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor_id = await session.scalar(
        select(Processor.id).where(Processor.key == "asset_download")
    )
    assert processor_id is not None
    run = await session.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.processor_id == processor_id, ProcessingRun.input_hash == identity)
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    if run is None:
        run = ProcessingRun(processor_id=processor_id, input_hash=identity)
        session.add(run)
        await session.flush()
    elif run.status in {"queued", "running", "completed"}:
        return {
            "id": run.id,
            "status": run.status,
            "version": plan["version"],
            "tier": plan["tier"],
            "download_id": plan["id"],
            "file_count": len(plan.get("files", [])),
            "size_bytes": sum(f.get("size", 0) for f in plan.get("files", [])),
        }

    run.status, run.error, run.finished_at = "queued", None, None
    total_size = sum(f.get("size", 0) for f in plan.get("files", []))
    run.metadata_json = {
        "request": {
            "version": plan["version"],
            "tier": plan["tier"],
            "download_id": plan["id"],
            "keys_commit": plan.get("keys_commit"),
            "file_count": len(plan.get("files", [])),
            "size_bytes": total_size,
        }
    }
    payload = dict(plan)
    payload["run_id"] = run.id
    await session.commit()
    try:
        await publish_media_job(payload, f"asset-download:{run.id}", "wuwa.asset-download.v1")
    except Exception as error:
        run.status = "enqueue_failed"
        run.error = "Queue confirmation failed; retry downloading client assets"
        await session.commit()
        raise RuntimeError("Asset download queue unavailable; retry the download") from error

    return {
        "id": run.id,
        "status": run.status,
        "version": plan["version"],
        "tier": plan["tier"],
        "download_id": plan["id"],
        "file_count": len(plan.get("files", [])),
        "size_bytes": total_size,
        "queued_at": datetime.now(UTC),
    }


async def check_client_update(session, tier: str = "hd") -> dict[str, Any]:
    plan = await asyncio.to_thread(discover_plan, None, tier)
    return await enqueue_client_plan(session, plan)
