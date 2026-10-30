"""Resolve authored textures and localized Wwise media, then publish exact references."""

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import wave
from contextlib import nullcontext
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image
from sqlalchemy import select, text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.ingestion.media_provenance import missing_media_targets, target_identity
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.broker import JobDeferred, job_lock
from wuwa_story_worker.character_voice_assets import prefetch_events, prefetch_media
from wuwa_story_worker.client_assets import (
    save_json,
    validate_plan,
    wait_workspace_lock,
    workspace_lock,
)
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.map_icons import build_icons
from wuwa_story_worker.media_diagnostics import publish_inventory
from wuwa_story_worker.media_provenance import sha256, source_provenance, temporary_entity_workspace
from wuwa_story_worker.media_workspace import (
    cleanup_workspace,
    finish_writes,
    package_root,
    resolve_workspace,
    workspace_lease,
)
from wuwa_story_worker.tooling import asset_workspace, tool_path

logger = logging.getLogger(__name__)


def event_media(exports: list[dict]) -> dict[str, int]:
    """Cooked media debug names identify languages; never guess from list order."""
    result = {}
    candidates = {}
    for export in exports:
        for entry in export.get("EventCookedData", {}).get("EventLanguageMap", []):
            for media in entry.get("Value", {}).get("Media", []):
                name = media.get("DebugName", "").replace(
                    "\\", "/").rsplit("/", 1)[-1]
                match = re.fullmatch(
                    r"(en|ja|ko|zh)_vo_[A-Za-z0-9_]+\.wav", name)
                if not match:
                    continue
                language = match[1]
                identity = media.get("MediaId")
                if (
                    type(identity) is not int
                    or identity <= 0
                    or media.get("MediaPathName") != f"Media/{identity}.wem"
                ):
                    raise ValueError("Invalid cooked voice media identity")
                values = candidates.setdefault(language, {})
                if name[3:] in values and values[name[3:]] != identity:
                    raise ValueError(
                        "Multiple media per language require authored sequencing")
                values[name[3:]] = identity
    # Localized base names agree across languages; a dialect suffix is a separate variant.
    common = set.intersection(
        *(set(values) for values in candidates.values())) if candidates else set()
    if len(common) == 1:
        name = common.pop()
        result = {language: values[name]
                  for language, values in candidates.items()}
    else:
        for language, values in candidates.items():
            if len(set(values.values())) != 1:
                raise ValueError(
                    "Multiple media per language require authored sequencing")
            result[language] = next(iter(values.values()))
    if not result:
        raise ValueError("Event does not contain a confirmed voice mapping")
    return result


async def tool(args, log, timeout=1800):
    def run():
        with log.open("w", encoding="utf-8") as stream:
            subprocess.run(
                [str(a) for a in args],
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=timeout,
            )

    await asyncio.to_thread(run)


@lru_cache(maxsize=4)
def client_voice_exports(root: Path, extractor_digest: str, cache_revision: int = 0) -> dict:
    """Index the pinned main-client export; Wwise media also lives in base paks."""
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    result = {}
    for asset_filter in ("WwiseAudio", "WwiseAudio/Events", "WwiseAudio_Generated/Media"):
        tag = hashlib.sha256(asset_filter.encode()).hexdigest()[:16]
        receipt = root / "exports" / tag / "manifest.json"
        if not receipt.exists():
            continue
        manifest = json.loads(receipt.read_text(encoding="utf-8"))
        if manifest["job_id"] != plan["id"] or manifest["extractor_sha256"] != extractor_digest:
            raise ValueError(
                "Cached voice export belongs to another client or extractor")
        for entry in manifest["files"]:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid cached export path")
            key = relative.as_posix().removeprefix("Client/Content/")
            if relative.suffix.casefold() in {".wem", ".uasset", ".uexp", ".ubulk"}:
                result[key] = (receipt.parent / "files" /
                               relative, entry["sha256"])
    return result


def verified_voice_export(entry) -> Path:
    path, digest = entry
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise ValueError(f"Cached voice export changed: {path.name}")
    return path


async def build_entity_files(root, targets, fmodel, converter, voice_root=None, provenance=None):
    images = [t for t in targets if t["kind"] == "image"]
    icons = {}
    if images:
        icons = await build_icons(
            root,
            fmodel,
            converter,
            [{"metadata_json": {"icon_source": t["path"]}} for t in images],
            entity_media=True,
            exact_textures=True,
        )
    files = []
    missing = []
    for target in images:
        icon = icons.get(target["path"])
        if icon is None:
            missing.append(target["path"])
            continue
        path = Path(icon["path"])
        with Image.open(path) as image:
            image.verify()
        files.append((target, path, "image", "image/png",
                     [Path(p) for p in icon["raw_paths"]]))
    for target in (target for target in targets if target['kind'] == 'spine'):
        from wuwa_story_worker.story_spine import build_spine_bundle
        try:
            bundle, originals = await build_spine_bundle(root, target['resource_id'], fmodel, converter)
        except BlockingIOError:
            raise
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
            logger.warning('story_spine.missing resource=%s error=%s',
                           target['resource_id'], str(error)[:500])
            missing.append(target['path'])
            continue
        files.append((target, bundle, 'unknown', 'application/zip', originals))
    voices = [t for t in targets if t["kind"] == "voice"]
    if not voices:
        return files, missing
    if voice_root is None and not os.getenv("WUWA_VOICE_ROOT"):
        raise RuntimeError(
            "Set WUWA_VOICE_ROOT to the completed multilingual voice download directory")
    voice_root = voice_root or Path(os.environ["WUWA_VOICE_ROOT"]).resolve()
    plan = json.loads((voice_root / "plan.json").read_text(encoding="utf-8"))
    status = json.loads(
        (voice_root / "status.json").read_text(encoding="utf-8"))
    base_plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    if (
        plan["version"] != base_plan["version"]
        or status.get("state") != "downloaded"
        or status.get("id") != plan["id"]
    ):
        raise ValueError(
            "A matching complete multilingual voice download is required")
    decoder = tool_path("WUWA_VGMSTREAM_PATH").resolve()
    extractor_digest = await asyncio.to_thread(sha256, fmodel)
    receipts = [root / "exports" / hashlib.sha256(value.encode()).hexdigest()[:16] / "manifest.json"
                for value in ("WwiseAudio", "WwiseAudio/Events", "WwiseAudio_Generated/Media")]
    revision = max(
        (p.stat().st_mtime_ns for p in receipts if p.exists()), default=0)
    cached = await asyncio.to_thread(client_voice_exports, root, extractor_digest, revision)
    sources = sorted({target["path"] for target in voices})
    for source in sources:
        if not re.fullmatch(r"/Game/Aki/WwiseAudio/Events/[A-Za-z0-9_]+\.[A-Za-z0-9_]+", source):
            raise ValueError("Unsupported character voice event path")
    events = await prefetch_events(root, sources, cached, fmodel, converter, tool, verified_voice_export)
    resolved = []
    for source in sources:
        if source not in events:
            missing.append(source)
            continue
        json_path, originals = events[source]
        try:
            cooked = json.loads(json_path.read_text(encoding="utf-8-sig"))
            mapping = event_media(cooked)
        except ValueError as error:
            logger.warning(
                "entity_media.voice_unresolved source=%s reason=%s", source, error)
            missing.append(source)
            continue
        for target in (t for t in voices if t["path"] == source):
            identity = mapping.get(target["language"])
            if identity is None:
                missing.append(source)
                continue
            resolved.append(
                (target, identity, json_path.parent, originals, cooked))
    media = await prefetch_media(root, voice_root, {r[1] for r in resolved}, cached,
                                 fmodel, tool, verified_voice_export)
    decoder_digest = await asyncio.to_thread(sha256, decoder)
    for target, identity, output, originals, cooked in resolved:
        entry = media.get(identity)
        if entry is None:
            missing.append(target["path"])
            continue
        wem, media_download_id, media_package_path = entry
        language = target["language"]
        destination = output / language / str(identity)
        destination.mkdir(parents=True, exist_ok=True)
        wav = destination / f"{language}-{identity}.wav"
        receipt = wav.with_suffix(".source.json")
        input_identity = {"wem_sha256": await asyncio.to_thread(sha256, wem), "decoder_sha256": decoder_digest}
        async with wait_workspace_lock(destination / "decode-lock"):
            saved = json.loads(receipt.read_text(
                encoding="utf-8")) if receipt.exists() else {}
            if (not wav.exists() or saved.get("input") != input_identity
                    or saved.get("sha256") != await asyncio.to_thread(sha256, wav)):
                partial = wav.with_suffix(".partial.wav")
                await finish_writes(tool([decoder, "-i", "-o", partial.resolve(), wem.resolve()], destination / "decoder.log", 120))
                with wave.open(str(partial), "rb") as audio:
                    if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                        raise ValueError("Empty decoded character voice")
                partial.replace(wav)
                save_json(receipt, {"input": input_identity, "sha256": await asyncio.to_thread(sha256, wav)})
        if provenance is not None:
            provenance[(target["path"], language)] = {
                "voice_plan_id": plan["id"], "media_download_id": media_download_id,
                "media_package_path": media_package_path,
                "voice_event": target["path"], "media_id": identity,
                "event_cooked_data": [e.get("EventCookedData", {}) for e in cooked],
            }
        files.append((target, wav, "audio_wav",
                     "audio/wav", [wem, *originals]))
    return files, missing


async def process_entity_media(payload: dict) -> None:
    request = MediaRequest.model_validate(payload["request"])
    try:
        async with workspace_lease(request.workspace_id):
            await _locked_process_entity_media(payload)
    finally:
        await cleanup_workspace(request.workspace_id)


async def _locked_process_entity_media(payload: dict) -> None:
    if (
        payload.get("schema_version") != 1
        or payload.get("job_type") != "media.entities"
        or type(payload.get("run_id")) is not int
    ):
        raise ValueError("Invalid entity media job")
    async with engine.connect() as connection, job_lock(connection, -payload["run_id"]):
        try:
            await _process_entity_media(payload, connection)
        except BlockingIOError as error:
            await connection.rollback()
            await update_admin_run(payload["run_id"], "entity_media", "waiting_dependency", error="Asset workspace is in use by another export; retrying after it finishes")
            raise JobDeferred("Asset workspace is in use") from error
        except Exception as error:
            await connection.rollback()
            async with SessionFactory(bind=connection) as session:
                run = await session.get(ProcessingRun, payload["run_id"])
                if (
                    run
                    and run.metadata_json.get("payload") == payload
                    and run.status not in ("completed", "partial", "cancelled")
                ):
                    run.status = "failed"
                    run.error = str(error)[:2000]
                    run.finished_at = datetime.now(UTC)
                    await session.commit()
            raise


async def _process_entity_media(payload: dict, connection) -> None:
    request = MediaRequest.model_validate(payload["request"])
    client = (asset_workspace() / "assets"
              / f"{request.asset_version}-{request.tier}-{request.download_id[:16]}")
    if request.workspace_id is not None:
        workspace = nullcontext(resolve_workspace(
            client, request.workspace_id, base=asset_workspace()))
    else:
        workspace = temporary_entity_workspace(client)
    with workspace as root:
        await finish_writes(_process_entity_workspace(payload, connection, root))


async def _process_entity_workspace(payload: dict, connection, root: Path) -> None:
    request = MediaRequest.model_validate(payload["request"])
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    validate_plan(plan)
    status = json.loads((root / "status.json").read_text(encoding="utf-8"))
    if status.get("state") != "downloaded" or status.get("id") != plan["id"]:
        raise ValueError("A matching completed client download is required")
    if (
        plan["id"] != request.download_id
        or plan["version"] != request.asset_version
        or plan["tier"] != request.tier
    ):
        raise ValueError("Media job does not match downloaded client")
    async with SessionFactory(bind=connection) as session:
        run = await session.get(ProcessingRun, payload["run_id"])
        if run is None or run.metadata_json.get("payload") != payload:
            raise ValueError("Media job is not the pinned backend request")
        if run.status in ("completed", "partial", "cancelled"):
            return
        lock_root = root / "entity-jobs" / str(run.id)
        lock_root.mkdir(parents=True, exist_ok=True)
        with workspace_lock(lock_root):
            run.status = "running"
            run.error = None
            await session.commit()
            try:
                storage = S3Storage(get_settings())
                tool_names = ["WUWA_FMODEL_PATH",
                              "WUWA_TEXTURE_CONVERTER_PATH"]
                if any(t["kind"] == "voice" for t in payload["targets"]):
                    tool_names.append("WUWA_VGMSTREAM_PATH")
                tools = {name: await asyncio.to_thread(sha256, tool_path(name)) for name in tool_names}
                source = {"asset_version": request.asset_version, "download_id": request.download_id,
                          "tier": request.tier, "voice_plan_id": request.voice_plan_id, "tools": tools}
                voice_root = (package_root(request.model_dump(), "voices", request.voice_plan_id, base=asset_workspace())
                              if request.voice_plan_id else Path(os.environ["WUWA_VOICE_ROOT"]).resolve()
                              if os.getenv("WUWA_VOICE_ROOT") else None)
                if voice_root is not None and request.voice_plan_id is None and "voice" in request.kinds:
                    source["voice_plan_id"] = json.loads(
                        (voice_root / "plan.json").read_text(encoding="utf-8"))["id"]
                targets = await missing_media_targets(
                    session, payload["targets"], source=source, storage=storage,
                    require_spine_playback=get_settings().story_spine_playback_enabled)
                missing_identities = {target_identity(t) for t in targets}
                skipped = len(payload["targets"]) - len(targets)
                voice_provenance = {}
                files, missing = [], []
                if targets:
                    files, missing = await build_entity_files(
                        root,
                        targets,
                        tool_path("WUWA_FMODEL_PATH"),
                        tool_path("WUWA_TEXTURE_CONVERTER_PATH"),
                        voice_root=voice_root,
                        provenance=voice_provenance,
                    )
                entries = []
                for target in payload["targets"]:
                    matches = [path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
                               for resolved, path, *_ in files if resolved == target]
                    entries.append({"expected": target["path"], "language": target["language"],
                                    "status": "already_published" if target_identity(target) not in missing_identities else "found" if matches else "not_resolved", "matches": matches})
                # Shared client manifests already inventory the full export. A character
                # job must not rescan hundreds of thousands of unrelated assets.
                inventory_paths = tuple(sorted({p for _, path, _, _, originals in files
                                                for p in (path, *originals)}))
                inventory = await publish_inventory(root, paths=inventory_paths)
                report = {"schema_version": 1, **inventory, "asset_version": request.asset_version,
                          "game_version": request.game_version, "entries": entries,
                          "requested": len(entries), "found": len(entries) - len(targets) + len(files),
                          "skipped": skipped, "missing": len(targets) - len(files)}
                run.raw_output = {"media_report": report, "published": 0}
                await session.commit()
                await storage.ensure_bucket()
                service = FileRegistrationService(storage)
                published_count = 0
                unsupported_spine = []
                for target, path, kind, mime, originals in files:
                    await session.execute(text("SELECT pg_advisory_xact_lock(:id)"),
                                          {"id": target["owner"]})
                    provenance = await asyncio.to_thread(
                        source_provenance, request, target, originals, tools,
                        {'spine_recipe': target['spine_recipe']} if target['kind'] == 'spine' else voice_provenance.get(
                            (target["path"], target["language"])),
                    )
                    published = await service.register_file(session, path, kind, mime_type=mime)
                    for original_path in originals:
                        original = await service.register_file(
                            session,
                            original_path,
                            "audio_wem" if original_path.suffix == ".wem" else "unknown",
                        )
                        variant = await service.register_variant(
                            session, original.id, published.id, "entity_media_decode", run.id,
                        )
                        metadata = variant.metadata_json or {}
                        sources = list(metadata.get("game_sources", []))
                        if provenance not in sources:
                            sources.append(provenance)
                        variant.metadata_json = {**metadata, "game_source": provenance,
                                                 "game_sources": sources}
                    reference_type = target.get('reference_type') or (
                        "entity_image" if target["kind"] == "image" else "character_voice"
                    )
                    reference = await session.scalar(
                        select(FileReference).where(
                            FileReference.owner_node_id == target["owner"],
                            FileReference.release_id == target["release_id"],
                            FileReference.reference_type == reference_type,
                            FileReference.source_path == target["path"],
                            FileReference.source_name == target["language"],
                            FileReference.metadata_json["asset_version"].astext
                            == request.asset_version,
                        )
                    )
                    if reference is None:
                        reference = await service.register_reference(
                            session,
                            file_id=published.id,
                            reference_type=reference_type,
                            owner_node_id=target["owner"],
                            release_id=target["release_id"],
                            source_path=target["path"],
                            source_name=target["language"],
                        )
                    reference.file_id = published.id
                    playback = None
                    if target['kind'] == 'spine' and get_settings().story_spine_playback_enabled:
                        from wuwa_story_worker.story_spine import publish_open_playback
                        playback = await publish_open_playback(session, path, target, published, service,
                                                               storage, run.id, request.asset_version)
                        if not playback['variants'] or playback['unsupported']:
                            unsupported_spine.append({'path': target['path'], 'diagnostics': playback['unsupported']})
                    reference.metadata_json = {
                        **({'playback': playback} if playback else {}),
                        "asset_version": request.asset_version,
                        "download_id": request.download_id,
                        "language": target["language"],
                        "game_source": provenance,
                    }
                    # Keep files, variants and provenance atomic for this target;
                    # a later target or deployment must not roll back earlier media.
                    run.raw_output = {"media_report": report,
                                      "published": published_count + 1}
                    await session.commit()
                    published_count += 1
                run.status = "partial" if missing or unsupported_spine else "completed"
                run.raw_output = {
                    "media_report": report,
                    "skipped": skipped,
                    "published": published_count,
                    "images": sum(t["kind"] == "image" for t, *_ in files),
                    "unsupported_spine": unsupported_spine,
                    "spine_bundles": sum(t['kind'] == 'spine' for t, *_ in files),
                    "voice_tracks": sum(t["kind"] == "voice" for t, *_ in files),
                    "missing_images": sorted(set(missing) & {t["path"] for t in payload["targets"] if t["kind"] == "image"}),
                    "missing_spine": sorted(set(missing) & {t['path'] for t in payload['targets'] if t['kind'] == 'spine'}),
                    "missing_voices": sorted(set(missing) & {t["path"] for t in payload["targets"] if t["kind"] == "voice"}),
                }
                run.finished_at = datetime.now(UTC)
                await session.commit()
                logger.info("entity_media.completed run=%s result=%s",
                            run.id, run.raw_output)
            except Exception as error:
                await session.rollback()
                run = await session.get(ProcessingRun, payload["run_id"])
                run.status = "failed"
                run.error = str(error)[:2000]
                run.finished_at = datetime.now(UTC)
                await session.commit()
                raise
