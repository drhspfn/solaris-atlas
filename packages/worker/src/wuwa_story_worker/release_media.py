"""Execute the media tasks queued by a snapshot import, with pinned dependencies."""

import asyncio
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy import select, text
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.client_assets import discover_plan, inspect_installed_clients
from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.ingestion.media_jobs import enqueue_entity_media

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.broker import JobDeferred
from wuwa_story_worker.client_assets import download_plan, save_json, workspace_lock
from wuwa_story_worker.cutscene_import import import_cutscene_recipe
from wuwa_story_worker.cutscene_plan import plan_cutscene
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.tooling import asset_workspace, tool_path
from wuwa_story_worker.voice_import import import_voice_sample

logger = logging.getLogger(__name__)


def client_root(result):
    return asset_workspace() / "assets" / f"{result['asset_version']}-{result['tier']}-{result['download_id'][:16]}"


async def prepare(run, session):
    await update_admin_run(run.id, "release_media", "running",
                           raw_output={"stage": "client_discovery"})
    logger.info("release_media.started run=%s kind=prepare stage=client_discovery", run.id)
    plan = run.metadata_json.get("client_plan")
    if plan is None:
        installed = await asyncio.to_thread(inspect_installed_clients, asset_workspace())
        ready = [item for item in installed if item.get("state") == "downloaded"]
        if ready:
            latest = max(ready, key=lambda item: tuple(map(int, item["version"].split("."))))
            root = asset_workspace() / "assets" / f"{latest['version']}-{latest['tier']}-{latest['download_id'][:16]}"
            plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
        else:
            plan = await asyncio.to_thread(discover_plan, None, "hd")
        run.metadata_json = {**run.metadata_json, "client_plan": plan}
        await session.commit()
    await update_admin_run(run.id, "release_media", "running", raw_output={
        "stage": "client_download", "asset_version": plan["version"], "total": len(plan["files"])})
    root = await asyncio.to_thread(download_plan, plan, asset_workspace(),
                                   int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4")))
    return {"asset_version": plan["version"], "download_id": plan["id"], "tier": plan["tier"],
            "stage": "client_ready"}


async def image_batch(payload, parent, run, session):
    child_id = (run.raw_output or {}).get("entity_run_id")
    child = await session.get(ProcessingRun, child_id) if child_id else None
    if child is None:
        request = MediaRequest(download_id=parent["download_id"], asset_version=parent["asset_version"],
                               tier=parent["tier"], game_version=payload["game_version"],
                               release_id=payload["release_id"],
                               entities=payload["targets"], kinds=["voice" if payload["kind"] == "character_voices" else "image"],
                               voice_plan_id=parent.get("voice_plan_id"))
        try:
            child = await enqueue_entity_media(session, request, retry_partial=True)
        except ValueError as error:
            if str(error) == "No authored media references for this request":
                return {"images": 0, "stage": "no_authored_images"}, "completed"
            raise
        run.raw_output = {"entity_run_id": child.id, "asset_version": parent["asset_version"]}
        await session.commit()
    await session.refresh(child)
    if child.status in {"queued", "running", "pending", "waiting_dependency"}:
        raise JobDeferred("Image extraction is queued or running")
    if child.status not in {"completed", "partial"}:
        raise RuntimeError(f"Image extraction task #{child.id}: {child.error or child.status}")
    return {**(child.raw_output or {}), "entity_run_id": child.id}, child.status


def merge_exports(receipts, destination):
    """Link cached exports; do not copy gigabytes of movies for each cutscene."""
    destination.mkdir(parents=True, exist_ok=True)
    with workspace_lock(destination):
        for receipt in receipts:
            source = receipt if receipt.is_dir() else receipt.parent / "files"
            for path in source.rglob("*"):
                if not path.is_file():
                    continue
                target = destination / path.relative_to(source)
                if target.exists():
                    if target.stat().st_size != path.stat().st_size:
                        raise ValueError("Conflicting cached media exports")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(path, target)
    return destination


def video_database(root):
    for path in sorted(root.rglob("*.db")):
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if {"videodata", "videosound"}.issubset(tables):
                    return path
        except sqlite3.DatabaseError:
            continue
    raise ValueError("Exported client has no VideoData/VideoSound database")


async def cutscene_assets(payload, parent):
    root = client_root(parent)
    fmodel = tool_path("WUWA_FMODEL_PATH")
    receipts = [await export_assets(root, fmodel, value)
                for value in ("ConfigDB", "Aki/Sequence", "WwiseAudio", "KuroPublicConfig")]
    assets = await asyncio.to_thread(merge_exports, receipts, root / "release-media" / "files")
    config = await asyncio.to_thread(video_database, assets)
    with sqlite3.connect(config.resolve().as_uri() + "?mode=ro", uri=True) as db:
        cg_ids = {row[0] for name in payload["targets"] for row in db.execute("SELECT CgId FROM videodata WHERE CgName = ?", (name,))}
    from wuwa_story_worker.entity_media import tool
    from wuwa_story_worker.voice_bootstrap import discover_video_plan
    from wuwa_story_worker.voice_packages import download_voice_plan
    plan = await asyncio.to_thread(discover_video_plan, parent["asset_version"], assets, parent["tier"], cg_ids)
    if plan["files"]:
        video_root = await asyncio.to_thread(download_voice_plan, plan, asset_workspace() / "videos",
                                             concurrency=int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4")))
        destination = video_root / "exports" / "files"
        destination.mkdir(parents=True, exist_ok=True)
        log = video_root / "exports" / "export.log"
        with workspace_lock(video_root):
            await tool([fmodel, video_root / "game", "@" + str(root / "keys.txt"), destination, "Aki/Movies"], log, 7200)
            output = log.read_text(encoding="utf-8", errors="replace")
            if "[Fail]" in output or "[Error]" in output or not any(destination.rglob("*.mp4")):
                raise RuntimeError(f"Video archive export failed; inspect {log}")
        await asyncio.to_thread(merge_exports, [destination.parent / "receipt.json"], assets)
    return {**parent, "stage": "cutscene_assets_ready"}, "completed"


async def cutscene(payload, parent):
    root = client_root(parent)
    assets = root / "release-media" / "files"
    voice_roots = [asset_workspace() / "voices" / f"{parent['asset_version']}-{parent['voice_plan_id'][:16]}"] if parent.get("voice_plan_id") else sorted((asset_workspace() / "voices").glob(parent["asset_version"] + "-*"))
    for voice_root in voice_roots:
        marker = voice_root / "audio-export.json"
        if marker.is_file():
            exported = json.loads(marker.read_text(encoding="utf-8"))
            plan = json.loads((voice_root / "plan.json").read_text(encoding="utf-8"))
            if parent.get("voice_plan_id") and plan.get("id") != parent["voice_plan_id"]:
                raise ValueError("Cutscene voice export does not match its pinned package plan")
            if exported.get("plan_id") == plan.get("id"):
                await asyncio.to_thread(merge_exports, [voice_root / "plot-audio"], assets)
    config = await asyncio.to_thread(video_database, assets)
    missing_assets = []
    recipe = await asyncio.to_thread(plan_cutscene, config, assets, payload["targets"][0], parent["asset_version"], missing_assets=missing_assets)
    work = root / "release-media" / str(payload["run_id"])
    work.mkdir(parents=True, exist_ok=True)
    recipe_path = work / "recipe.json"
    save_json(recipe_path, recipe)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is missing from the worker image")
    result = await import_cutscene_recipe(recipe_path, assets, assets, assets, work,
                                         tool_path("WUWA_VGMSTREAM_PATH"), Path(ffmpeg), missing_assets=missing_assets)
    return {**result, "asset_version": parent["asset_version"], "missing_assets": missing_assets}, "partial" if missing_assets else "completed"


async def voice_packages(parent):
    from wuwa_story_worker.voice_bootstrap import discover_client_voices
    from wuwa_story_worker.voice_packages import download_voice_plan

    root = client_root(parent)
    receipt = await export_assets(root, tool_path("WUWA_FMODEL_PATH"), "KuroPublicConfig")
    plan = await asyncio.to_thread(discover_client_voices, parent["asset_version"], receipt.parent / "files")
    voice_root = await asyncio.to_thread(download_voice_plan, plan, asset_workspace() / "voices",
                                         concurrency=int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4")))
    from wuwa_story_worker.entity_media import tool
    extracted = voice_root / "plot-audio"
    extracted.mkdir(exist_ok=True)
    log = extracted / "export.log"
    with workspace_lock(voice_root):
        await tool([tool_path("WUWA_FMODEL_PATH"), voice_root / "game",
                    "@" + str(root / "keys.txt"), extracted, "Audio"], log, 7200)
        output = log.read_text(encoding="utf-8", errors="replace")
        if "[Fail]" in output or "[Error]" in output or not any(path.suffix.casefold() == ".wem" for path in extracted.rglob("*")):
            failures = [line for line in output.splitlines() if "[Fail]" in line or "[Error]" in line]
            detail = "; ".join(failures[:3])[:1200] or "No WEM files matched the exported audio paths"
            raise RuntimeError(f"Voice archive export failed: {detail}; inspect {log}")
    save_json(voice_root / "audio-export.json", {"plan_id": plan["id"]})
    return {**parent, "voice_plan_id": plan["id"], "stage": "voice_packages_ready"}, "completed"


async def voices(payload, parent):
    extracted = asset_workspace() / "voices" / f"{parent['asset_version']}-{parent['voice_plan_id'][:16]}" / "plot-audio"
    result = await import_voice_sample(extracted, tool_path("WUWA_VGMSTREAM_PATH"),
                                      parent["asset_version"], names=set(payload["targets"]), require_all_languages=False)
    return result, "partial" if result.get("missing_voices") else "completed"


async def process_release_media(payload):
    if payload.get("schema_version") != 1 or payload.get("job_type") != "media.release" or type(payload.get("run_id")) is not int:
        raise ValueError("Invalid release media task")
    async with engine.connect() as connection:
        await connection.execute(text("SELECT pg_advisory_lock(702, :id)"), {"id": payload["run_id"]})
        await connection.commit()
        try:
            async with SessionFactory(bind=connection) as session:
                run = await session.get(ProcessingRun, payload["run_id"])
                if run is None or run.metadata_json.get("payload") != payload:
                    raise ValueError("Media task is not the pinned backend request")
                if run.status == "completed":
                    return
                if payload["kind"] == "prepare":
                    result, status = await prepare(run, session), "completed"
                else:
                    parent = await session.get(ProcessingRun, payload["parent_id"])
                    if parent is None or parent.metadata_json.get("request", {}).get("release_id") != payload["release_id"]:
                        raise ValueError("Media task dependency belongs to another snapshot")
                    if parent.status != "completed":
                        if parent.status in {"failed", "enqueue_failed", "blocked"}:
                            await update_admin_run(run.id, "release_media", "blocked", error=f"Client preparation #{parent.id} failed; retry the media import")
                            return
                        raise JobDeferred("Client preparation is still queued or running")
                    await update_admin_run(run.id, "release_media", "running")
                    if payload["kind"] in {"images", "character_voices"}:
                        result, status = await image_batch(payload, parent.raw_output, run, session)
                    elif payload["kind"] == "cutscene_assets":
                        result, status = await cutscene_assets(payload, parent.raw_output)
                    elif payload["kind"] == "voice_packages":
                        result, status = await voice_packages(parent.raw_output)
                    elif payload["kind"] == "cutscene":
                        voice_job = await session.scalar(select(ProcessingRun).where(
                            ProcessingRun.processor_id == run.processor_id,
                            ProcessingRun.metadata_json["request"]["release_id"].astext == str(payload["release_id"]),
                            ProcessingRun.metadata_json["request"]["kind"].astext == "voice_packages",
                        ).order_by(ProcessingRun.id.desc()).limit(1))
                        if voice_job is None:
                            raise ValueError("Missing voice package dependency for cutscene import")
                        if voice_job.status in {"failed", "blocked", "enqueue_failed"}:
                            await update_admin_run(run.id, "release_media", "blocked",
                                                   error=f"Voice preparation #{voice_job.id} failed; retry the media import")
                            return
                        if voice_job.status != "completed":
                            raise JobDeferred("Waiting for multilingual cutscene audio packages")
                        if voice_job.raw_output["download_id"] != parent.raw_output["download_id"]:
                            raise ValueError("Cutscene and voice packages belong to different clients")
                        result, status = await cutscene(payload, {**parent.raw_output,
                                                               "voice_plan_id": voice_job.raw_output["voice_plan_id"]})
                    elif payload["kind"] == "voices":
                        result, status = await voices(payload, parent.raw_output)
                    else:
                        raise ValueError("Unsupported release media task kind")
                await update_admin_run(run.id, "release_media", status, raw_output=result)
                logger.info("release_media.finished run=%s kind=%s status=%s", run.id, payload["kind"], status)
        except JobDeferred as error:
            await connection.rollback()
            await update_admin_run(payload["run_id"], "release_media", "waiting_dependency", error=str(error))
            raise
        except BlockingIOError as error:
            await connection.rollback()
            await update_admin_run(payload["run_id"], "release_media", "waiting_dependency", error="Asset workspace is in use by another export; retrying after it finishes")
            raise JobDeferred("Asset workspace is in use") from error
        except Exception as error:
            await connection.rollback()
            await update_admin_run(payload["run_id"], "release_media", "failed", error=f"{type(error).__name__}: {error}"[:2000])
            raise
        finally:
            await connection.rollback()
            await connection.execute(text("SELECT pg_advisory_unlock(702, :id)"), {"id": payload["run_id"]})
            await connection.commit()
