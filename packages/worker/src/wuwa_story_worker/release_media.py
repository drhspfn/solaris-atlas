"""Execute the media tasks queued by a snapshot import, with pinned dependencies."""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import select
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.core import AuthoredTreeImport
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.client_assets import discover_plan, inspect_installed_clients
from wuwa_story.ingestion.client_events import store_client_events
from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.ingestion.game_events import CLIENT_SOURCE, SOURCE
from wuwa_story.ingestion.media_jobs import enqueue_entity_media
from wuwa_story.ingestion.media_provenance import missing_media_targets
from wuwa_story.ingestion.release_media import enqueue_event_images, enqueue_release_media
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.archive_index import (
    ensure_archive_index,
    index_voice_paths,
    indexed_audio_paths,
    indexed_package_paths,
    voice_filter_batches,
)
from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.broker import JobDeferred, job_lock
from wuwa_story_worker.client_assets import download_plan, save_json, workspace_lock
from wuwa_story_worker.cutscene_import import import_cutscene_recipe, missing_cutscene_identities
from wuwa_story_worker.cutscene_plan import engine_path, plan_cutscene
from wuwa_story_worker.cutscene_sources import prepare_base_movies
from wuwa_story_worker.event_catalog import client_event_record, read_event_catalog
from wuwa_story_worker.event_resources import annotate_image_packages, event_resources
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.map_icons import build_icons
from wuwa_story_worker.media_diagnostics import publish_inventory, voice_report
from wuwa_story_worker.media_provenance import package_path, sha256
from wuwa_story_worker.media_workspace import (
    bind_package,
    cleanup_workspace,
    finish_writes,
    package_root,
    remove_job_output,
    resolve_workspace,
    run_workspace_id,
    workspace_lease,
)
from wuwa_story_worker.quest_tree_order import store_tree_order, tree_records
from wuwa_story_worker.tooling import asset_workspace, tool_path
from wuwa_story_worker.voice_import import (
    exported_voices,
    import_voice_sample,
    missing_global_voice_identities,
    missing_global_voice_paths,
    voice_path_roster,
)

logger = logging.getLogger(__name__)


async def source_context(parent, *, voice=False, video=False):
    names = ["WUWA_FMODEL_PATH", "WUWA_TEXTURE_CONVERTER_PATH"]
    if voice or video:
        names.append("WUWA_VGMSTREAM_PATH")
    tools = {name: await asyncio.to_thread(sha256, tool_path(name)) for name in names}
    if video:
        tools["WUWA_WWISER_PATH"] = await asyncio.to_thread(sha256, tool_path("WUWA_WWISER_PATH"))
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is missing from the worker image")
        tools["ffmpeg"] = await asyncio.to_thread(sha256, Path(ffmpeg))
    return {key: parent[key] for key in ("asset_version", "download_id", "tier", "voice_plan_id", "video_plan_id")
            if key in parent} | {"tools": tools}


def client_root(result):
    root = asset_workspace() / "assets" / f"{result['asset_version']}-{result['tier']}-{result['download_id'][:16]}"
    return resolve_workspace(root, result.get("workspace_id"), base=asset_workspace())


async def cleanup_published_output(parent, run_id, kind):
    if parent.get("workspace_id") is None or run_id is None:
        return
    try:
        await finish_writes(asyncio.to_thread(remove_job_output, parent, run_id, kind, base=asset_workspace()))
    except OSError:
        # Publication is already durable. Patch GC can retry failed disk cleanup.
        logger.exception("media_workspace.job_cleanup_failed workspace=%s run=%s kind=%s",
                         parent["workspace_id"], run_id, kind)


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
    root = asset_workspace() / "assets" / f"{plan['version']}-{plan['tier']}-{plan['id'][:16]}"
    def ready():
        try:
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            return (status.get("state") == "downloaded" and status.get("id") == plan["id"]
                    and (root / "keys.txt").is_file()
                    and all((root / "game" / item["path"]).is_file()
                            and (root / "game" / item["path"]).stat().st_size == item["size"]
                            for item in plan["files"]))
        except (OSError, ValueError):
            return False
    if not await asyncio.to_thread(ready):
        root = await asyncio.to_thread(download_plan, plan, asset_workspace(),
                                       int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4")))
    result = {"asset_version": plan["version"], "download_id": plan["id"], "tier": plan["tier"],
              "workspace_id": run.id, "stage": "client_ready"}
    await asyncio.to_thread(client_root, result)
    return result


async def image_batch(payload, parent, run, session):
    child_id = (run.raw_output or {}).get("entity_run_id")
    child = await session.get(ProcessingRun, child_id) if child_id else None
    if child is None:
        request = MediaRequest(download_id=parent["download_id"], asset_version=parent["asset_version"],
                               tier=parent["tier"], game_version=payload["game_version"],
                               release_id=payload["release_id"],
                               entities=payload["targets"], kinds=["voice" if payload["kind"] == "character_voices" else "image"],
                               voice_plan_id=parent.get("voice_plan_id"),
                               **({"workspace_id": parent["workspace_id"]} if parent.get("workspace_id") else {}))
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


async def import_tree_orders(parent, session):
    # Different patch imports may all use the same current client. Index it once.
    key = int.from_bytes(hashlib.sha256(parent["download_id"].encode()).digest()[:4], "big", signed=True)
    async with engine.connect() as lock_connection, job_lock(lock_connection, 704, key):
        existing = await session.get(AuthoredTreeImport, (parent["asset_version"], parent["download_id"]))
        if existing:
            return {"asset_version": parent["asset_version"], "trees": existing.tree_count,
                    "unresolved": 0, "reused": True}, "completed"
        await session.commit()
        result, status = await _import_tree_orders(parent, session)
        if status == "completed":
            session.add(AuthoredTreeImport(asset_version=parent["asset_version"],
                                           download_id=parent["download_id"], tree_count=result["trees"]))
            await session.commit()
        return result, status


async def _import_tree_orders(parent, session):
    root = client_root(parent)
    extractor = tool_path("WUWA_FMODEL_PATH")
    config = await export_assets(root, extractor, "ConfigDB")
    errors = []
    total = 0
    for kind, folder, dbname in (
        ("quest", "Quests", "db_QuestNodeData.db"),
        ("level_play", "LevelPlays", "db_LevelPlayNodeData.db"),
    ):
        receipt = await export_assets(root, extractor, f"Config/UniverseEditorConfig/{folder}/")
        databases = list((config.parent / "files").rglob(dbname))
        files = list((receipt.parent / "files").rglob("*.bytes"))
        if len(databases) != 1 or not files:
            raise ValueError(f"Missing client tree resources for {kind}")
        records = await asyncio.to_thread(
            list, tree_records(databases[0], files, kind, parent["asset_version"], parent["download_id"], errors)
        )
        for start in range(0, len(records), 25):
            total += await store_tree_order(session, records[start:start + 25])
            await session.commit()
    logger.info("release_media.tree_order asset_version=%s stored=%s unresolved=%s",
                parent["asset_version"], total, len(errors))
    return {"asset_version": parent["asset_version"], "trees": total,
            "unresolved": len(errors), "errors": errors[:100]}, "partial" if errors else "completed"


async def event_catalog(payload, parent, session):
    receipt = await export_assets(client_root(parent), tool_path("WUWA_FMODEL_PATH"), "ConfigDB")
    configs = list((receipt.parent / "files").rglob("db_activity.db"))
    if len(configs) != 1:
        raise ValueError(f"Expected one client Activity database, found {len(configs)}")
    if payload["targets"] == ["client-catalog:v2"]:
        readers_receipt = await export_assets(client_root(parent), tool_path("WUWA_FMODEL_PATH"),
                                              "JavaScript/Core/Define/Config")
        readers = list((readers_receipt.parent /
                       "files").rglob("Config/Activity.js"))
        if len(readers) != 1:
            raise ValueError("Expected one client Activity schema reader")
        records = await asyncio.to_thread(read_event_catalog, configs[0].parent, readers[0].parent)
        missing_packages = await annotate_image_packages(client_root(parent), records)
        result = await store_client_events(session, records, parent["asset_version"], parent["download_id"])
        await session.commit()
        notices = await enqueue_event_images(session, payload, ["en"], kind="event_notices")
        artwork_paths = result.pop("artwork_paths")
        images = await enqueue_event_images(session, payload, artwork_paths)
        resources = await enqueue_event_images(session, payload, result.pop("resource_events"), kind="event_resources")
        logger.info("release_media.client_events records=%s events=%s missing=%s image_tasks=%s",
                    result["catalog_records"], result["events"], len(result["unresolved"]), images["tasks"])
        return {**parent, **result, "stage": "event_catalog_ready", "source": CLIENT_SOURCE,
                "image_tasks": images, "resource_tasks": resources, "notice_tasks": notices,
                "requested": result["catalog_records"] + len(artwork_paths) + len(missing_packages),
                "found": result["events"] + len(artwork_paths),
                "missing": len(result["unresolved"]) + len(missing_packages),
                "missing_image_packages": missing_packages,
                "media_report": {"entries": result["unresolved"] + missing_packages}}, (
                    "partial" if result["unresolved"] or missing_packages else "completed")
    records = await asyncio.to_thread(read_event_catalog, configs[0].parent)
    identities = [target.split("@", 1)[0] for target in payload["targets"]]
    events = list(await session.scalars(select(GameEvent).where(GameEvent.source == SOURCE,
                                                              GameEvent.source_id.in_(identities))
                                       .order_by(GameEvent.id).with_for_update()))
    entries = []
    for event in events:
        record = client_event_record(records, event.source_id)
        english = (record or {}).get("locales", {}).get("en")
        if not english:
            entries.append({"expected": event.source_id, "status": "not_resolved",
                            "matches": [], "title_key": (record or {}).get("title_key"),
                            "config_present": bool(record),
                            "nearby": [record["title_key"]] if record else []})
            continue
        presentation = {**record, "asset_version": parent["asset_version"]}
        event.title = english["title"]
        event.description = english["description"] or event.description
        # Archive previews are specific artwork; replace missing artwork and
        # gacha share thumbnails with the actual client presentation background.
        if record["banner_path"] and (not event.banner_path or record["table"] == "GachaViewInfo"):
            event.banner_path = record["banner_path"]
        presentation["banner_path"] = event.banner_path
        event.source_data = {**event.source_data, "client": presentation}
        occurrences = list(await session.scalars(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == event.id,
            GameEventOccurrence.game_version == ".".join(parent["asset_version"].split(".")[:2]),
        )))
        for occurrence in occurrences:
            occurrence.source_data = {**occurrence.source_data, "client": presentation}
            occurrence.banner_path = event.banner_path
        entries.append({"expected": event.source_id, "status": "found",
                        "matches": [record["title_key"]], "title": event.title,
                        "banner_path": event.banner_path})
    await session.commit()
    found = sum(entry["status"] == "found" for entry in entries)
    logger.info("release_media.event_catalog requested=%s found=%s asset_version=%s",
                len(entries), found, parent["asset_version"])
    report = {"asset_version": parent["asset_version"], "requested": len(entries),
              "found": found, "missing": len(entries) - found, "entries": entries}
    return {**parent, "stage": "event_catalog_ready", "requested": len(entries),
            "found": found, "missing": len(entries) - found,
            "media_report": report, "entries": entries}, "completed"


async def event_images(payload, parent, session):
    """Extract and publish schedule artwork by its authored client texture path."""
    root = client_root(parent)
    targets = payload["targets"]
    unresolved = []
    if targets and ":" in targets[0] and not targets[0].startswith("/Game/"):
        events = list(await session.scalars(select(GameEvent).where(GameEvent.source == SOURCE,
            GameEvent.source_id.in_([target.split("@", 1)[0] for target in targets]))))
        targets = sorted({event.banner_path for event in events if event.banner_path})
        unresolved = [{"expected": event.source_id, "status": "not_resolved", "matches": []}
                      for event in events if not event.banner_path]
    await session.commit()  # Do not hold task/catalog read locks during texture extraction.
    sources = {
        path: path if path.startswith("/Game/") else "/Game/Aki/UI/UIResources/" + path.lstrip("/")
        for path in targets
    }
    storage = S3Storage(get_settings())
    source_identity = await source_context(parent)
    requested = [{"owner": None, "release_id": payload["release_id"], "path": path,
                  "language": None, "kind": "image"} for path in sources]
    missing = await missing_media_targets(session, requested, source=source_identity,
                                         storage=storage, reference_type="event_banner")
    missing_paths = {target["path"] for target in missing}
    reused = [{"expected": path, "status": "found", "matches": [], "reused": True}
              for path in sources if path not in missing_paths]
    await session.commit()  # Persist any cross-release references before extraction.
    markers = [{"metadata_json": {"icon_source": source}} for path, source in sources.items()
               if path in missing_paths]
    icons = await build_icons(
        root, tool_path("WUWA_FMODEL_PATH"), tool_path("WUWA_TEXTURE_CONVERTER_PATH"),
        markers, entity_media=True, exact_textures=True,
    ) if markers else {}
    service = None
    entries = unresolved + reused
    for original, source in sources.items():
        if original not in missing_paths:
            continue
        icon = icons.get(source)
        if icon is None:
            entries.append({"expected": original, "status": "not_resolved", "matches": []})
            continue
        if service is None:
            await storage.ensure_bucket()
            service = FileRegistrationService(storage)
        image_path = Path(icon["path"])
        image = await service.register_file(session, image_path, "image", mime_type="image/png")
        for raw_path in icon["raw_paths"]:
            raw_file = await service.register_file(session, Path(raw_path), "unknown")
            await service.register_variant(session, raw_file.id, image.id, "event_banner_png")
        reference = await session.scalar(select(FileReference).where(
            FileReference.release_id == payload["release_id"],
            FileReference.reference_type == "event_banner",
            FileReference.source_path == original,
            FileReference.metadata_json["asset_version"].astext == parent["asset_version"],
            FileReference.metadata_json["download_id"].astext == parent["download_id"],
        ))
        if reference is None:
            reference = await service.register_reference(
                session, file_id=image.id, reference_type="event_banner",
                release_id=payload["release_id"], source_path=original,
            )
        reference.file_id = image.id
        reference.metadata_json = {"asset_version": parent["asset_version"],
                                   "download_id": parent["download_id"],
                                   "game_source": {**source_identity, "schema_version": 1,
                                       "package_path": original.split(".", 1)[0],
                                       "source_files": [{"package_path": package_path(Path(path)),
                                           "sha256": await asyncio.to_thread(sha256, Path(path))}
                                           for path in icon["raw_paths"]]}}
        entries.append({"expected": original, "status": "found",
                        "matches": [image_path.name]})
    await session.commit()
    return {"asset_version": parent["asset_version"],
            "images": sum(entry["status"] == "found" for entry in entries),
            "missing_images": [entry["expected"] for entry in entries if entry["status"] != "found"],
            "media_report": {"entries": entries}, "requested": len(entries),
            "found": sum(entry["status"] == "found" for entry in entries),
            "missing": sum(entry["status"] != "found" for entry in entries),
            "entries": entries}, ("partial" if any(e["status"] != "found" for e in entries)
                                  else "completed")


def merge_exports(receipts, destination):
    """Link cached exports; do not copy gigabytes of movies for each cutscene."""
    destination.mkdir(parents=True, exist_ok=True)
    with workspace_lock(destination):
        marker = destination.parent / "merged-exports.json"
        merged = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        for receipt in receipts:
            source = receipt if receipt.is_dir() else receipt.parent / "files"
            identity_file = receipt if receipt.is_file() else source.parent / "audio-export.json"
            if not identity_file.is_file():
                identity_file = source
            stamp = identity_file.stat()
            identity = [stamp.st_mtime_ns, stamp.st_size]
            key = str(source.resolve())
            if merged.get(key) == identity:
                continue
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
            merged[key] = identity
            save_json(marker, merged)  # Only mark a fully merged source reusable.
    return destination


def video_database(root, *, cache=None):
    marker = cache or root / "video-database.json"
    if marker.is_file():
        relative = json.loads(marker.read_text(encoding="utf-8"))["path"]
        candidate = root / relative
        if candidate.resolve().is_relative_to(root.resolve()) and candidate.is_file():
            return candidate
    for path in sorted(root.rglob("*.db")):
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if {"videodata", "videosound"}.issubset(tables):
                    save_json(marker, {"path": path.relative_to(root).as_posix()})
                    return path
        except sqlite3.DatabaseError:
            continue
    raise ValueError("Exported client has no VideoData/VideoSound database")


async def cutscene_assets(payload, parent):
    context = await source_context(parent, video=True)
    missing = await missing_cutscene_identities({"cutscene:" + name for name in payload["targets"]},
                                               source_context=context)
    targets = [name for name in payload["targets"] if "cutscene:" + name in missing]
    if parent.get("workspace_id") and not parent.get("video_plan_id"):
        # Preparation has not resolved its final video package identity yet.
        # Leaves check reuse with that identity; retain exact wrappers for their fallback.
        targets = payload["targets"]
    if not targets:
        return {**parent, "stage": "cutscene_assets_reused", "reused": len(payload["targets"])}, "completed"
    root = client_root(parent)
    fmodel = tool_path("WUWA_FMODEL_PATH")
    filters = ("ConfigDB", "KuroPublicConfig") if parent.get("workspace_id") else (
        "ConfigDB", "Aki/Sequence", "WwiseAudio", "KuroPublicConfig")
    receipts = [await export_assets(root, fmodel, value) for value in filters]
    config = await asyncio.to_thread(video_database, receipts[0].parent / "files",
                                     cache=receipts[0].parent / "video-database.json")
    with closing(sqlite3.connect(config.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        videos = [(row[0], engine_path(row[1]).removeprefix("/Game/").split(".", 1)[0])
                  for name in targets for row in db.execute("SELECT CgId, BinData FROM videodata WHERE CgName = ?", (name,))]
    cg_ids = {cg_id for cg_id, _ in videos}
    unavailable = []
    available = {}
    index = None
    if parent.get("workspace_id"):
        # Historical config can retain wrappers removed from the current client.
        # Sound events come from the localized package in extract_cutscene_audio.
        paths = {path for _, path in videos}
        index = await ensure_archive_index(root, fmodel, root)
        available = await asyncio.to_thread(indexed_package_paths, index, paths)
        unavailable = sorted(paths - available.keys())
        cg_ids = {cg_id for cg_id, path in videos if path in available}
        for batch in voice_filter_batches(path.removesuffix(".uasset") for path in available.values()):
            receipts.append(await export_assets(root, fmodel, "|".join(batch)))
    assets = await asyncio.to_thread(merge_exports, receipts, root / "release-media" / "files")
    base_movies = await prepare_base_movies(root, assets, available.values(), fmodel, index=index)
    if base_movies["receipts"]:
        await asyncio.to_thread(merge_exports, base_movies["receipts"], assets)
    save_json(assets / "video-database.json", {"path": config.relative_to(receipts[0].parent / "files").as_posix()})
    from wuwa_story_worker.entity_media import tool
    from wuwa_story_worker.voice_bootstrap import discover_video_plan
    from wuwa_story_worker.voice_packages import download_voice_plan
    video_diagnostics = {}
    plan = await asyncio.to_thread(discover_video_plan, parent["asset_version"], assets, parent["tier"], cg_ids,
                                   diagnostics=video_diagnostics)
    if plan["files"]:
        video_root = await asyncio.to_thread(download_voice_plan, plan, asset_workspace() / "videos",
                                             concurrency=int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4")))
        video_root = await asyncio.to_thread(bind_package, parent, "videos", plan["id"], video_root,
                                             base=asset_workspace())
        destination = video_root / "exports" / "files"
        destination.mkdir(parents=True, exist_ok=True)
        log = video_root / "exports" / "export.log"
        with workspace_lock(video_root):
            await tool([fmodel, video_root / "game", "@" + str(root / "keys.txt"), destination, "Aki/Movies"], log, 7200)
            output = log.read_text(encoding="utf-8", errors="replace")
            if "[Fail]" in output or "[Error]" in output or not any(destination.rglob("*.mp4")):
                raise RuntimeError(f"Video archive export failed; inspect {log}")
        await asyncio.to_thread(merge_exports, [destination.parent / "receipt.json"], assets)
    inventory = await publish_inventory(assets)
    inventory["missing_video_packages"] = unavailable
    inventory["video_packages"] = video_diagnostics
    inventory["invalid_movie_sources"] = base_movies.get("errors", {})
    from wuwa_story_worker.cutscene_import import confined
    inventory["missing_movies"] = [path for path in sorted(set(base_movies["movies"].values()))
                                    if not confined(assets, path).is_file()]
    return {**parent, "video_plan_id": plan["id"], "stage": "cutscene_assets_ready", "media_report": inventory}, "completed"


async def cutscene(payload, parent):
    from wuwa_story_worker.cutscene_import import confined, movie_path

    context = await source_context(parent, video=True)
    if not await missing_cutscene_identities({"cutscene:" + payload["targets"][0]}, source_context=context):
        await cleanup_published_output(parent, payload.get("run_id"), "cutscene")
        return {**parent, "stage": "cutscene_reused", "reused": 1}, "completed"
    root = client_root(parent)
    assets = root / "release-media" / "files"
    config = await asyncio.to_thread(video_database, assets)
    missing_assets = []
    recipe = await asyncio.to_thread(plan_cutscene, config, assets, payload["targets"][0], parent["asset_version"], missing_assets=missing_assets)
    missing_wrappers = [video["asset"] for video in recipe["videos"]
                        if not confined(assets, "Client/Content/" + video["asset"].removeprefix("asset:ue:/Game/").rsplit(".", 1)[0] + ".uexp").is_file()]
    if missing_wrappers:
        # Keep the authored variant flow intact; never publish a different clip
        # or discard an unavailable variant to make the task look successful.
        await cleanup_published_output(parent, payload["run_id"], "cutscene")
        return {"videos": 0, "asset_version": parent["asset_version"], "missing_assets": missing_wrappers,
                "media_report": {"entries": [{"expected": path, "status": "not_in_archive", "matches": []}
                                             for path in missing_wrappers]}}, "partial"
    absent_movies = []
    invalid_movies = []
    for video in recipe["videos"]:
        package = video["asset"].removeprefix("asset:ue:/Game/").rsplit(".", 1)[0]
        wrapper = "Client/Content/" + package + ".uexp"
        try:
            path = movie_path(confined(assets, wrapper).read_bytes())
        except ValueError as error:
            invalid_movies.append({"expected": wrapper, "status": "invalid_source",
                                   "matches": [], "reason": str(error)})
            continue
        if not confined(assets, path).is_file():
            absent_movies.append(path)
    if absent_movies or invalid_movies:
        await cleanup_published_output(parent, payload["run_id"], "cutscene")
        return {"videos": 0, "asset_version": parent["asset_version"],
                "missing_assets": absent_movies + [entry["expected"] for entry in invalid_movies],
                "media_report": {"entries": [{"expected": path, "status": "not_in_archive", "matches": []}
                                             for path in absent_movies] + invalid_movies}}, "partial"
    voice_cache = root / "voices" if parent.get("workspace_id") else asset_workspace() / "voices"
    voice_roots = [package_root(parent, "voices", parent["voice_plan_id"], base=asset_workspace())] if parent.get("voice_plan_id") else sorted(voice_cache.glob(parent["asset_version"] + "-*"))
    if parent.get("workspace_id") and parent.get("voice_plan_id"):
        audio = await extract_cutscene_audio(parent, config, payload["targets"][0])
        await asyncio.to_thread(merge_exports, [audio], assets)
        voice_roots = []  # Selected banks/media are already linked for this cutscene.
    for voice_root in voice_roots:
        marker = voice_root / "audio-export.json"
        if marker.is_file():
            exported = json.loads(marker.read_text(encoding="utf-8"))
            plan = json.loads((voice_root / "plan.json").read_text(encoding="utf-8"))
            if parent.get("voice_plan_id") and plan.get("id") != parent["voice_plan_id"]:
                raise ValueError("Cutscene voice export does not match its pinned package plan")
            if exported.get("plan_id") == plan.get("id"):
                await asyncio.to_thread(merge_exports, [voice_root / "plot-audio"], assets)
    missing_assets = []  # Rebuild after the selected multilingual banks are available.
    recipe = await asyncio.to_thread(plan_cutscene, config, assets, payload["targets"][0], parent["asset_version"], missing_assets=missing_assets)
    work = root / "release-media" / str(payload["run_id"])
    work.mkdir(parents=True, exist_ok=True)
    recipe_path = work / "recipe.json"
    save_json(recipe_path, recipe)
    entries = []
    for video in recipe["videos"]:
        package = video["asset"].removeprefix("asset:ue:/Game/").rsplit(".", 1)[0]
        expected = "Client/Content/" + package + ".uexp"
        exported = confined(assets, expected)
        entries.append({"expected": expected, "status": "found" if exported.is_file() else "not_in_export",
                        "matches": [exported.relative_to(assets.resolve()).as_posix()] if exported.is_file() else []})
        for track in video.get("soundtrack", []):
            expected = track["bank"]
            entries.append({"expected": expected, "language": track.get("language"),
                            "status": "found" if (assets / expected).is_file() else "not_in_export",
                            "matches": [expected] if (assets / expected).is_file() else []})
    report = {**(parent.get("media_report") or {}), "schema_version": 1,
              "asset_version": parent["asset_version"], "game_version": payload["game_version"], "entries": entries}
    await update_admin_run(payload["run_id"], "release_media", "running", raw_output={"media_report": report})
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is missing from the worker image")
    result = await import_cutscene_recipe(recipe_path, assets, assets, assets, work,
                                         tool_path("WUWA_VGMSTREAM_PATH"), Path(ffmpeg), missing_assets=missing_assets,
                                         source_context=context)
    entries.extend({"expected": name, "status": "not_resolved", "matches": []} for name in missing_assets)
    report.update(requested=len(entries), found=sum(e["status"] == "found" for e in entries),
                  missing=sum(e["status"] != "found" for e in entries))
    await cleanup_published_output(parent, payload["run_id"], "cutscene")
    return {**result, "asset_version": parent["asset_version"], "missing_assets": missing_assets,
            "media_report": report}, "partial" if missing_assets else "completed"


async def voice_packages(parent):
    from wuwa_story_worker.voice_bootstrap import discover_client_voices
    from wuwa_story_worker.voice_packages import download_voice_plan

    voice_cache = client_root(parent) / "voices" if parent.get("workspace_id") else asset_workspace() / "voices"
    markers = sorted(voice_cache.glob(f"{parent['asset_version']}-*/audio-export.json"),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    for marker in markers:
        try:
            cached = json.loads((marker.parent / "plan.json").read_text(encoding="utf-8"))
            status = json.loads((marker.parent / "status.json").read_text(encoding="utf-8"))
            exported = json.loads(marker.read_text(encoding="utf-8"))
            if (cached["version"] == parent["asset_version"]
                    and status.get("state") == "downloaded" and status.get("id") == cached["id"]
                    and exported.get("plan_id") == cached["id"]):
                return {**parent, "voice_plan_id": cached["id"], "stage": "voice_packages_ready",
                        "media_report": exported.get("media_report", {})}, "completed"
        except (OSError, ValueError, KeyError):
            continue
    root = client_root(parent)
    receipt = await export_assets(root, tool_path("WUWA_FMODEL_PATH"), "KuroPublicConfig")
    plan = await asyncio.to_thread(discover_client_voices, parent["asset_version"], receipt.parent / "files")
    voice_root = await asyncio.to_thread(download_voice_plan, plan, asset_workspace() / "voices",
                                         concurrency=int(os.getenv("WUWA_ASSET_DOWNLOAD_CONCURRENCY", "4")))
    voice_root = await asyncio.to_thread(bind_package, parent, "voices", plan["id"], voice_root,
                                         base=asset_workspace())
    from wuwa_story_worker.entity_media import tool
    extracted = voice_root / "plot-audio"
    extracted.mkdir(exist_ok=True)
    log = extracted / "export.log"
    with workspace_lock(voice_root):
        index = voice_root / "archive-index.log"
        await tool([tool_path("WUWA_FMODEL_PATH"), voice_root / "game",
                    "@" + str(root / "keys.txt"), "--list"], index, 7200)
        if parent.get("workspace_id"):
            await asyncio.to_thread(index_voice_paths, index)
            inventory = await publish_inventory(extracted, index)
            save_json(voice_root / "audio-export.json", {"plan_id": plan["id"], "mode": "selective",
                                                       "media_report": inventory})
            return {**parent, "voice_plan_id": plan["id"], "stage": "voice_packages_ready",
                    "media_report": inventory}, "completed"
        await tool([tool_path("WUWA_FMODEL_PATH"), voice_root / "game",
                    "@" + str(root / "keys.txt"), extracted, "Audio"], log, 7200)
        output = log.read_text(encoding="utf-8", errors="replace")
        if "[Fail]" in output or "[Error]" in output or not any(path.suffix.casefold() == ".wem" for path in extracted.rglob("*")):
            failures = [line for line in output.splitlines() if "[Fail]" in line or "[Error]" in line]
            detail = "; ".join(failures[:3])[:1200] or "No WEM files matched the exported audio paths"
            raise RuntimeError(f"Voice archive export failed: {detail}; inspect {log}")
        inventory = await publish_inventory(extracted, index)
    # Character events can reference numeric WEM IDs in the main client.
    # Export once per pinned client instead of remounting all paks for each line.
    await export_assets(root, tool_path("WUWA_FMODEL_PATH"), "WwiseAudio/Events")
    await export_assets(root, tool_path("WUWA_FMODEL_PATH"), "WwiseAudio_Generated/Media")
    save_json(voice_root / "audio-export.json", {"plan_id": plan["id"], "media_report": inventory})
    return {**parent, "voice_plan_id": plan["id"], "stage": "voice_packages_ready",
            "media_report": inventory}, "completed"


def selected_voice_paths(index, names):
    expected = sorted({f"{language}_{name}{suffix}.wem".casefold() for name in names
                       for language in ("en", "ja", "ko", "zh") for suffix in ("", "_F", "_M")})
    return indexed_audio_paths(index, expected)


async def requested_voice_paths(parent, names):
    """Read the complete authored roster before deciding which files to reuse."""
    voice_root = package_root(parent, "voices", parent["voice_plan_id"], base=asset_workspace())
    index = voice_root / "archive-index.sqlite"
    if not index.is_file():
        await asyncio.to_thread(index_voice_paths, voice_root / "archive-index.log")
    return await asyncio.to_thread(selected_voice_paths, index, names)


async def extract_requested_voices(parent, names, destination=None, *, paths=None):
    """Export only missing archive paths, including authored Rover variants."""
    voice_root = package_root(parent, "voices", parent["voice_plan_id"], base=asset_workspace())
    selected = await requested_voice_paths(parent, names) if paths is None else sorted(paths)
    destination = destination or voice_root / "plot-audio"
    await extract_voice_paths(parent, selected, destination)
    if destination != voice_root / "plot-audio":
        # Diagnostics consume only this batch's paths, not the full archive log.
        (destination.parent / "archive-index.log").write_text(
            "".join(f"[File] {path}\n" for path in selected) + f"[Done] Listed {len(selected)} files\n",
            encoding="utf-8")


async def extract_voice_paths(parent, selected, extracted):
    voice_root = package_root(parent, "voices", parent["voice_plan_id"], base=asset_workspace())
    extracted.mkdir(parents=True, exist_ok=True)
    from wuwa_story_worker.entity_media import tool
    with workspace_lock(voice_root):
        missing = [path for path in selected if not (extracted / path).is_file()]
        # Each batch remounts the archives, so fill the bounded filter argument.
        for paths in voice_filter_batches(missing):
            log = voice_root / "selective-export.log"
            await tool([tool_path("WUWA_FMODEL_PATH"), voice_root / "game",
                        "@" + str(client_root(parent) / "keys.txt"), extracted,
                        "|".join(paths)], log, 7200)
            output = log.read_text(encoding="utf-8", errors="replace")
            if "[Fail]" in output or "[Error]" in output:
                raise RuntimeError(f"Requested voice export failed; inspect {log}")


async def extract_cutscene_audio(parent, config, name):
    """Follow authored event banks, then their exact Wwise media identifiers."""
    from wuwa_story_worker.wwise_banks import bank_media_ids

    voice_root = package_root(parent, "voices", parent["voice_plan_id"], base=asset_workspace())
    destination = voice_root / "cutscene-audio" / hashlib.sha256(name.encode()).hexdigest()[:16] / "files"
    index = voice_root / "archive-index.sqlite"
    if not index.is_file():
        await asyncio.to_thread(index_voice_paths, voice_root / "archive-index.log")
    with closing(sqlite3.connect(config.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        names = sorted({engine_path(row[0]).rsplit("/", 1)[-1].split(".", 1)[0].casefold() + ".bnk"
                        for row in db.execute("SELECT BinData FROM videosound WHERE CgName = ?", (name,))})
    banks = await asyncio.to_thread(indexed_audio_paths, index, names)
    await extract_voice_paths(parent, banks, destination)
    root = client_root(parent)
    fmodel = tool_path("WUWA_FMODEL_PATH")
    main_index = await ensure_archive_index(root, fmodel, root)
    main_banks = await asyncio.to_thread(indexed_audio_paths, main_index,
                                        set(names) - {Path(path).name.casefold() for path in banks})
    for batch in voice_filter_batches(main_banks):
        receipt = await export_assets(root, fmodel, "|".join(batch))
        await asyncio.to_thread(merge_exports, [receipt], destination)
    media = set()
    for path in [*banks, *main_banks]:
        bank = destination / path
        if bank.is_file():
            try:
                identities = bank_media_ids(await asyncio.to_thread(bank.read_bytes))
            except ValueError:
                continue  # The individual track reports its unsupported bank during publication.
            media.update(f"{identity}.wem" for identity in identities)
    paths = await asyncio.to_thread(indexed_audio_paths, index, media)
    await extract_voice_paths(parent, paths, destination)
    main_paths = await asyncio.to_thread(indexed_audio_paths, main_index,
                                        media - {Path(path).name.casefold() for path in paths})
    if banks or main_banks:
        main_paths += await asyncio.to_thread(indexed_audio_paths, main_index, ["init.bnk"])
    for batch in voice_filter_batches(main_paths):
        receipt = await export_assets(root, fmodel, "|".join(batch))
        await asyncio.to_thread(merge_exports, [receipt], destination)
    save_json(destination.parent / "audio-export.json", {"banks": [*banks, *main_banks], "media": [*paths, *main_paths]})
    return destination


async def voices(payload, parent):
    context = await source_context(parent, voice=True)
    scoped = parent.get("workspace_id") is not None
    names = set(payload["targets"])
    reused, missing_paths, absent_languages = set(), set(), []
    roster = {}
    if scoped:
        context["voice_paths"] = await requested_voice_paths(parent, names)
        roster = voice_path_roster(names, context["voice_paths"])
        missing_paths = await missing_global_voice_paths(names, context["voice_paths"], source_context=context)
        reused = set(context["voice_paths"]) - missing_paths
        absent_languages = [f"{language}_{name}" for name, tracks in roster.items()
                            for language in ("en", "ja", "ko", "zh")
                            if not any(track["language"] == language for track in tracks)]
        if not missing_paths:
            await cleanup_published_output(parent, payload.get("run_id"), "voices")
            return {**parent, "stage": "voices_reused", "reused": len(reused), "tracks": 0,
                    "missing_voices": absent_languages}, "partial" if absent_languages else "completed"
    else:
        names = await missing_global_voice_identities(names, source_context=context)
    if not names:
        return {**parent, "stage": "voices_reused", "reused": len(payload["targets"]), "tracks": 0}, "completed"
    voice_root = package_root(parent, "voices", parent["voice_plan_id"], base=asset_workspace())
    extracted = voice_root / "plot-audio"
    if scoped:
        extracted = voice_root / "voice-batches" / str(payload["run_id"]) / "files"
    from wuwa_story_worker.entity_media import tool
    inventory = parent.get("media_report")
    if inventory is None:
        # Existing successful package tasks predate inventories: reuse their pinned files.
        with workspace_lock(voice_root):
            marker = voice_root / "audio-export.json"
            saved = json.loads(marker.read_text(encoding="utf-8"))
            if saved.get("plan_id") != parent["voice_plan_id"]:
                raise ValueError("Voice export does not match its pinned package plan")
            inventory = saved.get("media_report")
            if inventory is None:
                index = voice_root / "archive-index.log"
                await tool([tool_path("WUWA_FMODEL_PATH"), voice_root / "game",
                            "@" + str(client_root(parent) / "keys.txt"), "--list"], index, 7200)
                inventory = await publish_inventory(extracted, index)
                save_json(marker, {**saved, "media_report": inventory})
    if scoped:
        await extract_requested_voices(parent, names, extracted, paths=missing_paths)
    grouped = await asyncio.to_thread(exported_voices, extracted, names)
    if scoped:
        # A retry can leave local copies of already published tracks in this batch.
        grouped = {name: {key: path for key, path in tracks.items()
                          if path.relative_to(extracted).as_posix() in missing_paths}
                   for name, tracks in grouped.items()}
        grouped = {name: tracks for name, tracks in grouped.items() if tracks}
    receipt = await export_assets(client_root(parent), tool_path("WUWA_FMODEL_PATH"), "ConfigDB")
    configs = list((receipt.parent / "files").rglob("db_plot_audio.db"))
    if len(configs) > 1:
        raise ValueError("Ambiguous client PlotAudio configuration")
    report = await asyncio.to_thread(voice_report, extracted, names, grouped,
                                     inventory=inventory, config=configs[0] if configs else None)
    if scoped:
        exported = {path.relative_to(extracted).as_posix()
                    for tracks in grouped.values() for path in tracks.values()}
        for entry in report["entries"]:
            language, name = entry["expected"].removesuffix(".wem").split("_", 1)
            paths = {track["path"] for track in roster[name] if track["language"] == language}
            entry["matches"] = sorted(paths & (exported | reused))
            entry["status"] = ("found" if paths and paths <= (exported | reused)
                               else "export_missing" if paths else "not_in_archives")
            if paths & reused:
                entry["reused"] = True
        report.update(found=sum(entry["status"] == "found" for entry in report["entries"]),
                      missing=sum(entry["status"] != "found" for entry in report["entries"]))
    report.update(asset_version=parent["asset_version"], game_version=payload["game_version"])
    await update_admin_run(payload["run_id"], "release_media", "running", raw_output={"media_report": report})
    # Repair verified archive hits that a previous export left behind. No fuzzy substitutions.
    missing_exports = [entry for entry in report["entries"] if entry["status"] == "export_missing"]
    if missing_exports and not scoped:
        with workspace_lock(extracted.parent):
            for entry in missing_exports:
                await tool([tool_path("WUWA_FMODEL_PATH"), voice_root / "game",
                            "@" + str(client_root(parent) / "keys.txt"), extracted,
                            entry["expected"].removesuffix(".wem")], extracted.parent / "repair-export.log", 1800)
        grouped = await asyncio.to_thread(exported_voices, extracted, names)
        report = await asyncio.to_thread(voice_report, extracted, names, grouped,
                                         inventory=inventory, config=configs[0] if configs else None)
    report["asset_version"] = parent["asset_version"]
    report["game_version"] = payload["game_version"]
    await update_admin_run(payload["run_id"], "release_media", "running", raw_output={"media_report": report})
    logger.info("release_media.voice_lookup run=%s requested=%s found=%s missing=%s asset_version=%s",
                payload["run_id"], report["requested"], report["found"], report["missing"], parent["asset_version"])
    context["path_map"] = {path.relative_to(extracted).as_posix(): path.relative_to(extracted).as_posix()
                           for tracks in grouped.values() for path in tracks.values()}
    result = await import_voice_sample(extracted, tool_path("WUWA_VGMSTREAM_PATH"),
                                      parent["asset_version"], names=set(grouped) if scoped else names,
                                      require_all_languages=False, grouped=grouped,
                                      source_context=context)
    if scoped:
        # Preserve authored files that extraction failed to produce, even when
        # another name in the batch was repaired successfully.
        result["missing_voices"] = sorted(set(result.get("missing_voices", []))
            | (missing_paths - exported) | set(absent_languages))
        result["reused"] = len(reused)
        await cleanup_published_output(parent, payload["run_id"], "voices")
    return {**result, "media_report": report}, "partial" if result.get("missing_voices") else "completed"


async def process_release_media(payload):
    if payload.get("schema_version") != 1 or payload.get("job_type") != "media.release" or type(payload.get("run_id")) is not int:
        raise ValueError("Invalid release media task")
    workspace_id = await run_workspace_id(payload["run_id"])
    try:
        async with workspace_lease(workspace_id):
            await finish_writes(_process_release_media(payload))
    finally:
        try:
            await cleanup_workspace(workspace_id)
        except Exception:
            # Cleanup is retryable independently; don't replay a successful import.
            logger.exception("media_workspace.cleanup_failed workspace=%s run=%s", workspace_id, payload["run_id"])


async def _process_release_media(payload):
    async with engine.connect() as connection, job_lock(connection, 702, payload["run_id"]):
        try:
            async with SessionFactory(bind=connection) as session:
                run = await session.get(ProcessingRun, payload["run_id"])
                if run is None or run.metadata_json.get("payload") != payload:
                    raise ValueError("Media task is not the pinned backend request")
                if run.status in {"completed", "partial", "cancelled"}:
                    return
                await session.commit()
                if payload["kind"] == "prepare":
                    result, status = await prepare(run, session), "completed"
                else:
                    parent = await session.get(ProcessingRun, payload["parent_id"])
                    if parent is None or parent.metadata_json.get("request", {}).get("release_id") != payload["release_id"]:
                        raise ValueError("Media task dependency belongs to another snapshot")
                    partial_resources = parent.status == "partial" and payload["kind"] in {"event_images", "event_resources", "event_notices"} and parent.metadata_json.get(
                        "request", {}).get("kind") in {"event_catalog", "event_resources"}
                    if parent.status != "completed" and not partial_resources:
                        if parent.status in {"failed", "enqueue_failed", "blocked", "cancelled"}:
                            await update_admin_run(run.id, "release_media", "blocked", error=f"Client preparation #{parent.id} failed; retry the media import")
                            return
                        raise JobDeferred("Client preparation is still queued or running")
                    if (parent.raw_output or {}).get("workspace_id") is None:
                        # Replayed pre-workspace coordinators must never export
                        # into retained downloads or reuse their old entity child.
                        run.raw_output = None
                        renewed = await enqueue_release_media(
                            session, payload["release_id"],
                            event_artwork_only=payload["kind"] in {"event_catalog", "event_images", "event_resources"},
                            notices_only=payload["kind"] == "event_notices")
                        logger.warning("release_media.legacy_workspace_renewed run=%s parent=%s release=%s queued=%s",
                                       run.id, parent.id, payload["release_id"], renewed["queued"])
                        raise JobDeferred("Legacy media dependency renewed; waiting for patch workspace preparation")
                    await session.commit()  # Dependency checks are complete; release read locks before I/O.
                    await update_admin_run(run.id, "release_media", "running")
                    if payload["kind"] in {"images", "character_voices"}:
                        result, status = await image_batch(payload, parent.raw_output, run, session)
                    elif payload["kind"] == "event_images":
                        result, status = await event_images(payload, parent.raw_output, session)
                    elif payload["kind"] == "event_catalog":
                        result, status = await event_catalog(payload, parent.raw_output, session)
                    elif payload["kind"] == "event_resources":
                        result, status = await event_resources(payload, parent.raw_output, session, client_root(parent.raw_output))
                    elif payload["kind"] == "event_notices":
                        from wuwa_story_worker.notices import import_notices
                        result, status = await import_notices(client_root(parent.raw_output), session)
                    elif payload["kind"] == "tree_order":
                        result, status = await import_tree_orders(parent.raw_output, session)
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
                        if voice_job.status in {"failed", "blocked", "enqueue_failed", "cancelled"}:
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
                if payload["kind"] != "prepare" and parent.raw_output.get("workspace_id"):
                    result = {**result, "workspace_id": parent.raw_output["workspace_id"]}
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
