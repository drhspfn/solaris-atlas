"""Administrative snapshot imports and ingestion overview."""

import asyncio
import hashlib
import subprocess
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.maps import MapMarker, TileMap
from wuwa_story.db.models.ops import GameRelease, ImportRun, ProcessingRun, Processor
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.github_snapshots import discover_remote_snapshots
from wuwa_story.ingestion.media_jobs import publish_media_job

DEFAULT_REPOSITORY = "https://github.com/Arikatsu/WutheringWaves_Data.git"

router = APIRouter(
    prefix="/admin/data-operations",
    tags=["data operations"],
    dependencies=[Depends(require_admin)],
)


class SnapshotImportRequest(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+$")


@router.get("")
async def overview(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    releases = list(
        await session.scalars(select(GameRelease).order_by(GameRelease.sequence.desc()))
    )
    import_rows = (
        await session.execute(
            select(ImportRun, GameRelease.game_version)
            .join(GameRelease, GameRelease.id == ImportRun.release_id)
            .order_by(ImportRun.id.desc())
            .limit(100)
        )
    ).all()
    map_rows = (
        await session.execute(
            select(
                TileMap.game_version,
                func.count(func.distinct(TileMap.game_map_id)),
                func.count(func.distinct(TileMap.asset_job_id)),
            )
            .group_by(TileMap.game_version)
            .order_by(TileMap.game_version.desc())
        )
    ).all()
    marker_counts = dict(
        (
            await session.execute(
                select(TileMap.game_version, func.count(func.distinct(MapMarker.id)))
                .join(
                    MapMarker,
                    (MapMarker.asset_job_id == TileMap.asset_job_id)
                    & (MapMarker.game_map_id == TileMap.game_map_id),
                )
                .group_by(TileMap.game_version)
            )
        ).all()
    )
    processing_rows = (
        await session.execute(
            select(ProcessingRun, Processor.key)
            .join(Processor, Processor.id == ProcessingRun.processor_id)
            .where(Processor.key == "snapshot_import")
            .order_by(ProcessingRun.id.desc())
            .limit(50)
        )
    ).all()
    return {
        "releases": [
            {
                "id": release.id,
                "sequence": release.sequence,
                "game_version": release.game_version,
                "resource_version": release.resource_version,
                "upstream_name": release.upstream_name,
                "upstream_commit": release.upstream_commit,
                "imported_at": release.imported_at,
            }
            for release in releases
        ],
        "imports": [
            {
                "id": run.id,
                "game_version": version,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "records_seen": run.records_seen,
                "records_created": run.records_created,
                "records_updated": run.records_updated,
                "records_failed": run.records_failed,
                "error": run.error,
            }
            for run, version in import_rows
        ],
        "snapshot_jobs": [
            {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "error": run.error,
                **(
                    run.metadata_json.get("request", {})
                    if isinstance(run.metadata_json, dict)
                    else {}
                ),
            }
            for run, _ in processing_rows
        ],
        "maps": [
            {
                "game_version": version,
                "map_count": maps,
                "asset_jobs": jobs,
                "marker_count": marker_counts.get(version, 0),
            }
            for version, maps, jobs in map_rows
        ],
    }


@router.post("/snapshots", status_code=202, dependencies=[Depends(require_csrf)])
async def enqueue_snapshot_import(
    request: SnapshotImportRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        discovered = await asyncio.to_thread(
            discover_remote_snapshots, DEFAULT_REPOSITORY, request.version, request.version
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise HTTPException(502, f"Could not resolve upstream patch {request.version}") from error
    snapshot = discovered[0]
    identity = hashlib.sha256(
        f"{DEFAULT_REPOSITORY}:{snapshot.branch}:{snapshot.commit}".encode()
    ).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": int.from_bytes(identity[:7], "big")},
    )
    await session.execute(
        insert(Processor)
        .values(
            key="snapshot_import",
            version="1",
            description="Build and import a pinned upstream snapshot",
        )
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor_id = await session.scalar(
        select(Processor.id).where(Processor.key == "snapshot_import")
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
            "version": snapshot.branch,
            "commit": snapshot.commit,
        }
    run.status, run.error, run.finished_at = "queued", None, None
    run.metadata_json = {
        "request": {
            "version": snapshot.branch,
            "commit": snapshot.commit,
            "repository": DEFAULT_REPOSITORY,
        }
    }
    payload = {
        "schema_version": 1,
        "job_type": "snapshot.build_import",
        "version": snapshot.branch,
        "repository_url": DEFAULT_REPOSITORY,
        "branch": snapshot.branch,
        "commit": snapshot.commit,
        "run_id": run.id,
    }
    await session.commit()
    try:
        await publish_media_job(payload, f"snapshot-import:{run.id}", "wuwa.snapshot-build.v1")
    except Exception as error:
        run.status = "enqueue_failed"
        run.error = "Queue confirmation failed; retry this snapshot import"
        await session.commit()
        raise HTTPException(503, "Snapshot queue unavailable; retry the import") from error
    return {
        "id": run.id,
        "status": run.status,
        "version": snapshot.branch,
        "commit": snapshot.commit,
        "queued_at": datetime.now(UTC),
    }
