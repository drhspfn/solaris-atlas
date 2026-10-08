"""Administrative snapshot imports, game client downloads and ingestion overview."""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.maps import MapMarker, TileMap
from wuwa_story.db.models.ops import GameRelease, ImportRun, ProcessingRun, Processor
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.client_assets import (
    discover_plan,
    get_live_launcher_info,
    inspect_installed_clients,
)
from wuwa_story.ingestion.github_snapshots import discover_remote_snapshots
from wuwa_story.ingestion.media_jobs import publish_media_job

DEFAULT_REPOSITORY = "https://github.com/Arikatsu/WutheringWaves_Data.git"
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/data-operations",
    tags=["data operations"],
    dependencies=[Depends(require_admin)],
)


class _PayloadModel(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _parse_raw_json_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            try:
                return json.loads(data)
            except Exception:
                return data
        return data


class SnapshotImportRequest(_PayloadModel):
    version: str = Field(pattern=r"^\d+\.\d+$")


class ClientAssetDownloadRequest(_PayloadModel):
    version: str | None = Field(default=None, pattern=r"^\d+\.\d+(\.\d+)?$")
    tier: Literal["sd", "hd", "uhd"] = "hd"


class MapBuildRequest(_PayloadModel):
    version: str = Field(default="3.7.0", pattern=r"^\d+\.\d+(\.\d+)?$")
    tier: Literal["sd", "hd", "uhd"] = "hd"
    download_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _get_asset_workspace() -> Path:
    return Path(os.getenv("WUWA_ASSET_WORKSPACE", "./var/client-assets")).resolve()


async def _inspect_client_assets(session: AsyncSession) -> dict[str, Any]:
    live_info = await asyncio.to_thread(get_live_launcher_info, 10)
    workspace = _get_asset_workspace()
    installed = await asyncio.to_thread(inspect_installed_clients, workspace)

    asset_runs = list(
        await session.scalars(
            select(ProcessingRun)
            .join(Processor, Processor.id == ProcessingRun.processor_id)
            .where(Processor.key == "asset_download")
            .order_by(ProcessingRun.id.desc())
            .limit(20)
        )
    )

    active_download = None
    for run in asset_runs:
        if run.status in {"queued", "running"}:
            active_download = {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at,
                **(
                    run.metadata_json.get("request", {})
                    if isinstance(run.metadata_json, dict)
                    else {}
                ),
            }
            break

    active_client = None
    for item in installed:
        if item.get("state") == "downloaded":
            active_client = item
            break

    if active_client is None:
        for run in asset_runs:
            if run.status == "completed" and isinstance(run.metadata_json, dict):
                req = run.metadata_json.get("request", {})
                active_client = {
                    "version": req.get("version"),
                    "tier": req.get("tier"),
                    "download_id": req.get("download_id"),
                    "state": "downloaded",
                    "file_count": req.get("file_count", 0),
                    "size_bytes": req.get("size_bytes", 0),
                }
                break

    return {
        "live_version": live_info.get("version"),
        "installed": installed,
        "active_client": active_client,
        "active_download": active_download,
    }


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

    asset_processing_rows = (
        await session.execute(
            select(ProcessingRun, Processor.key)
            .join(Processor, Processor.id == ProcessingRun.processor_id)
            .where(Processor.key == "asset_download")
            .order_by(ProcessingRun.id.desc())
            .limit(50)
        )
    ).all()

    map_processing_rows = (
        await session.execute(
            select(ProcessingRun, Processor.key)
            .join(Processor, Processor.id == ProcessingRun.processor_id)
            .where(Processor.key == "map_build")
            .order_by(ProcessingRun.id.desc())
            .limit(50)
        )
    ).all()

    client_assets_data = await _inspect_client_assets(session)

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
        "asset_jobs": [
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
            for run, _ in asset_processing_rows
        ],
        "map_jobs": [
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
            for run, _ in map_processing_rows
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
        "client_assets": client_assets_data,
    }


@router.post("/snapshots", status_code=202, dependencies=[Depends(require_csrf)])
async def enqueue_snapshot_import(
    request: SnapshotImportRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        discovered = await asyncio.to_thread(
            discover_remote_snapshots, DEFAULT_REPOSITORY, request.version, request.version
        )
    except ValueError as error:
        raise HTTPException(400, f"Upstream patch {request.version} was not found") from error
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        logger.exception("Upstream snapshot discovery failed version=%s", request.version)
        raise HTTPException(
            400, "Could not contact the upstream repository; retry the patch import"
        ) from error
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


@router.post("/client-download", status_code=202, dependencies=[Depends(require_csrf)])
async def enqueue_client_download(
    request: ClientAssetDownloadRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        plan = await asyncio.to_thread(discover_plan, request.version, request.tier)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(400, f"Could not resolve official client plan: {error}") from error

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
        raise HTTPException(503, "Asset download queue unavailable; retry the download") from error

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


@router.post("/maps/build", status_code=202, dependencies=[Depends(require_csrf)])
async def enqueue_map_build(
    request: MapBuildRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    workspace = _get_asset_workspace()
    installed = await asyncio.to_thread(inspect_installed_clients, workspace)

    target_client = None
    if request.download_id:
        target_client = next(
            (c for c in installed if c.get("download_id") == request.download_id), None
        )
    else:
        target_client = next(
            (
                c
                for c in installed
                if c.get("version") == request.version and c.get("tier") == request.tier
            ),
            None,
        )

    # If not on local disk, check if completed ProcessingRun exists
    if target_client is None:
        query = (
            select(ProcessingRun)
            .join(Processor, Processor.id == ProcessingRun.processor_id)
            .where(Processor.key == "asset_download", ProcessingRun.status == "completed")
        )
        completed_runs = list(await session.scalars(query.order_by(ProcessingRun.id.desc())))
        for r in completed_runs:
            req = r.metadata_json.get("request", {}) if isinstance(r.metadata_json, dict) else {}
            if request.download_id and req.get("download_id") == request.download_id:
                target_client = req
                break
            elif (
                req.get("version") == request.version
                and req.get("tier") == request.tier
            ):
                target_client = req
                break

    if target_client is None:
        raise HTTPException(
            400,
            f"No completed game client download found for version {request.version} ({request.tier}). "
            "Please download the game client first.",
        )

    download_id = target_client.get("download_id")
    version = target_client.get("version", request.version)
    tier = target_client.get("tier", request.tier)

    identity = hashlib.sha256(f"map_build:{download_id}".encode()).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": int.from_bytes(identity[:7], "big")},
    )
    await session.execute(
        insert(Processor)
        .values(
            key="map_build",
            version="1",
            description="Extract and build interactive maps from client assets",
        )
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor_id = await session.scalar(select(Processor.id).where(Processor.key == "map_build"))
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
    elif run.status in {"queued", "running"}:
        return {
            "id": run.id,
            "status": run.status,
            "version": version,
            "tier": tier,
            "download_id": download_id,
        }

    run.status, run.error, run.finished_at = "queued", None, None
    run.metadata_json = {
        "request": {
            "version": version,
            "tier": tier,
            "download_id": download_id,
        }
    }
    payload = {
        "schema_version": 1,
        "job_type": "assets.maps",
        "download_id": download_id,
        "version": version,
        "tier": tier,
        "run_id": run.id,
    }
    await session.commit()
    try:
        await publish_media_job(payload, f"map-build:{run.id}", "wuwa.asset-extract.v1")
    except Exception as error:
        run.status = "enqueue_failed"
        run.error = "Queue confirmation failed; retry the map build job"
        await session.commit()
        raise HTTPException(503, "Map extraction queue unavailable; retry the build") from error

    return {
        "id": run.id,
        "status": run.status,
        "version": version,
        "tier": tier,
        "download_id": download_id,
        "queued_at": datetime.now(UTC),
    }
