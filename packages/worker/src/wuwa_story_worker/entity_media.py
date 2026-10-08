"""Resolve authored textures and localized Wwise media, then publish exact references."""

import asyncio
import json
import logging
import os
import re
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from sqlalchemy import select, text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.broker import JobDeferred
from wuwa_story_worker.client_assets import validate_plan, workspace_lock
from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.map_icons import build_icons
from wuwa_story_worker.tooling import asset_workspace, tool_path

logger = logging.getLogger(__name__)


def event_media(exports: list[dict]) -> dict[str, int]:
    """Cooked media debug names identify languages; never guess from list order."""
    result = {}
    for export in exports:
        for entry in export.get("EventCookedData", {}).get("EventLanguageMap", []):
            for media in entry.get("Value", {}).get("Media", []):
                name = media.get("DebugName", "").replace("\\", "/").rsplit("/", 1)[-1]
                match = re.fullmatch(r"(en|ja|ko|zh)_vo_[A-Za-z0-9_]+\.wav", name)
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
                if language in result and result[language] != identity:
                    raise ValueError("Multiple media per language require authored sequencing")
                result[language] = identity
    if set(result) != {"en", "ja", "ko", "zh"}:
        raise ValueError("Event does not contain a confirmed four-language voice mapping")
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


async def build_entity_files(root, targets, fmodel, converter, voice_root=None):
    images = [t for t in targets if t["kind"] == "image"]
    icons = {}
    if images:
        with workspace_lock(root / "entity-image-cache"):
            icons = await build_icons(
                root,
                fmodel,
                converter,
                [{"metadata_json": {"icon_source": t["path"]}} for t in images],
                entity_media=True,
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
        files.append((target, path, "image", "image/png", [Path(p) for p in icon["raw_paths"]]))
    voices = [t for t in targets if t["kind"] == "voice"]
    if not voices:
        if not files:
            raise ValueError("None of the requested textures could be decoded")
        return files, missing
    if voice_root is None and not os.getenv("WUWA_VOICE_ROOT"):
        raise RuntimeError("Set WUWA_VOICE_ROOT to the completed multilingual voice download directory")
    voice_root = voice_root or Path(os.environ["WUWA_VOICE_ROOT"]).resolve()
    plan = json.loads((voice_root / "plan.json").read_text(encoding="utf-8"))
    status = json.loads((voice_root / "status.json").read_text(encoding="utf-8"))
    base_plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    if (
        plan["version"] != base_plan["version"]
        or status.get("state") != "downloaded"
        or status.get("id") != plan["id"]
    ):
        raise ValueError("A matching complete multilingual voice download is required")
    decoder = tool_path("WUWA_VGMSTREAM_PATH").resolve()
    for source in sorted({t["path"] for t in voices}):
        if not re.fullmatch(r"/Game/Aki/WwiseAudio/Events/[A-Za-z0-9_]+\.[A-Za-z0-9_]+", source):
            raise ValueError("Unsupported character voice event path")
        receipt = await export_assets(root, fmodel, source.split(".")[0].removeprefix("/Game/"))
        raw = receipt.parent / "files"
        output = receipt.parent / "cooked-event"
        output.mkdir(exist_ok=True)
        await tool(
            [
                converter.resolve(),
                "-i",
                raw.resolve(),
                "-g",
                "GAME_WutheringWaves",
                "-p",
                "*" + source.rsplit("/", 1)[-1].split(".")[0] + "*",
                "-f",
                "json",
                "-o",
                output.resolve(),
                "-y",
            ],
            output / "converter.log",
        )
        jsons = list(output.rglob(source.rsplit("/", 1)[-1].split(".")[0] + ".json"))
        if len(jsons) != 1:
            raise ValueError("Expected one cooked event export")
        mapping = event_media(json.loads(jsons[0].read_text(encoding="utf-8-sig")))
        for language, identity in mapping.items():
            destination = output / language
            destination.mkdir(exist_ok=True)
            with workspace_lock(voice_root):
                await tool(
                    [
                        fmodel.resolve(),
                        (voice_root / "game").resolve(),
                        "@" + str((root / "keys.txt").resolve()),
                        destination.resolve(),
                        f"/{identity}.wem",
                    ],
                    destination / "fmodel.log",
                )
            wem = list(destination.rglob(f"{identity}.wem"))
            if len(wem) != 1 or "[Fail]" in (destination / "fmodel.log").read_text(
                encoding="utf-8"
            ):
                raise ValueError(f"Voice media not extracted: {language}/{identity}")
            wav = destination / f"{language}-{identity}.wav"
            partial = wav.with_suffix(".partial.wav")
            await tool(
                [decoder, "-i", "-o", partial.resolve(), wem[0].resolve()],
                destination / "decoder.log",
                120,
            )
            with wave.open(str(partial), "rb") as audio:
                if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                    raise ValueError("Empty decoded character voice")
            partial.replace(wav)
            for target in voices:
                if target["path"] == source and target["language"] == language:
                    files.append(
                        (target, wav, "audio_wav", "audio/wav", [wem[0], *raw.rglob("*.bnk")])
                    )
    return files, missing


async def process_entity_media(payload: dict) -> None:
    if (
        payload.get("schema_version") != 1
        or payload.get("job_type") != "media.entities"
        or type(payload.get("run_id")) is not int
    ):
        raise ValueError("Invalid entity media job")
    async with engine.connect() as connection:
        await connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": -payload["run_id"]})
        await connection.commit()
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
                    and run.status not in ("completed", "partial")
                ):
                    run.status = "failed"
                    run.error = str(error)[:2000]
                    run.finished_at = datetime.now(UTC)
                    await session.commit()
            raise
        finally:
            await connection.rollback()
            await connection.execute(
                text("SELECT pg_advisory_unlock(:id)"), {"id": -payload["run_id"]}
            )
            await connection.commit()


async def _process_entity_media(payload: dict, connection) -> None:
    request = MediaRequest.model_validate(payload["request"])
    root = (
        asset_workspace()
        / "assets"
        / f"{request.asset_version}-{request.tier}-{request.download_id[:16]}"
    )
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
        if run.status in ("completed", "partial"):
            return
        lock_root = root / "entity-jobs" / str(run.id)
        lock_root.mkdir(parents=True, exist_ok=True)
        with workspace_lock(lock_root):
            run.status = "running"
            run.error = None
            await session.commit()
            try:
                files, missing = await build_entity_files(
                    root,
                    payload["targets"],
                    tool_path("WUWA_FMODEL_PATH"),
                    tool_path("WUWA_TEXTURE_CONVERTER_PATH"),
                    voice_root=(asset_workspace() / "voices" / f"{request.asset_version}-{request.voice_plan_id[:16]}") if request.voice_plan_id else None,
                )
                storage = S3Storage(get_settings())
                await storage.ensure_bucket()
                service = FileRegistrationService(storage)
                for owner in sorted({t["owner"] for t, *_ in files}):
                    await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": owner})
                for target, path, kind, mime, originals in files:
                    published = await service.register_file(session, path, kind, mime_type=mime)
                    for original_path in originals:
                        original = await service.register_file(
                            session,
                            original_path,
                            "audio_wem" if original_path.suffix == ".wem" else "unknown",
                        )
                        await service.register_variant(
                            session, original.id, published.id, "entity_media_decode"
                        )
                    reference_type = (
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
                    reference.metadata_json = {
                        "asset_version": request.asset_version,
                        "download_id": request.download_id,
                        "language": target["language"],
                    }
                run.status = "partial" if missing else "completed"
                run.raw_output = {
                    "images": sum(t["kind"] == "image" for t, *_ in files),
                    "voice_tracks": sum(t["kind"] == "voice" for t, *_ in files),
                    "missing_images": sorted(set(missing)),
                }
                run.finished_at = datetime.now(UTC)
                await session.commit()
                logger.info("entity_media.completed run=%s result=%s", run.id, run.raw_output)
            except Exception as error:
                await session.rollback()
                run = await session.get(ProcessingRun, payload["run_id"])
                run.status = "failed"
                run.error = str(error)[:2000]
                run.finished_at = datetime.now(UTC)
                await session.commit()
                raise
