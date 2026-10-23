"""Publish an extracted voice sample with exact filename and language identities."""

import asyncio
import json
import re
import subprocess
import wave
from pathlib import Path

from sqlalchemy import select, text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.core import VoiceReference
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.media_provenance import package_path
from wuwa_story_worker.publication_provenance import (
    pinned_source,
    publication_source,
    verified_references,
)
from wuwa_story_worker.voice_packages import LANGUAGES


async def missing_global_voice_identities(names: set[str], *, source_context: dict,
                                          storage=None) -> set[str]:
    """Before export, find names needing any owner/language/rover publication.

    A persisted complete track inventory is required; legacy or partial imports retry.
    No extracted files are needed. source_context uses the importer's same pinned IDs/tools.
    """
    source = pinned_source(source_context)
    if not names:
        return set()
    storage = storage or S3Storage(get_settings())
    async with SessionFactory() as session:
        voices = list(await session.scalars(select(VoiceReference).where(
            VoiceReference.file_name.in_(names))))
        refs = await verified_references(session, storage, "voice_audio", source,
                                         owner_ids={voice.node_id for voice in voices})
        missing = set()
        for voice in voices:
            owned = [ref for ref in refs if ref.owner_node_id == voice.node_id
                     and ref.metadata_json.get("voice_name") == voice.file_name]
            inventory = next((ref.metadata_json.get("voice_tracks") for ref in owned
                              if ref.metadata_json.get("voice_inventory_complete")), None)
            present = {(ref.source_name, ref.source_path) for ref in owned}
            if (not inventory or {track["language"] for track in inventory} != set(LANGUAGES)
                    or any((track["source_name"], track["path"]) not in present for track in inventory)):
                missing.add(voice.file_name)
        return missing | (names - {voice.file_name for voice in voices})


def voice_package_path(root, path, context):
    mapping = (context or {}).get("path_map", {})
    return mapping.get(path.relative_to(root).as_posix(), mapping.get(path.name, package_path(path)))


def voice_identity(filename: str) -> tuple[str, str]:
    match = re.fullmatch(r"(en|ja|ko|zh)_(vo_[A-Za-z0-9_]+)\.wem", filename)
    if not match:
        raise ValueError("Expected an exact localized PlotAudio filename")
    return match[1], match[2]


def exported_voices(root: Path, names: set[str] | None = None) -> dict:
    """Resolve exact authored filenames and explicit F/M variants; no fuzzy matching."""
    grouped = {}
    requested = {name.casefold(): name for name in names} if names is not None else None
    if requested is not None and len(requested) != len(names):
        raise ValueError("Conflicting case-insensitive voice identities")
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() != ".wem":
            continue
        match = re.fullmatch(r"(en|ja|ko|zh)_(vo_[A-Za-z0-9_]+)\.wem", path.name, re.IGNORECASE)
        if not match:
            continue
        language, name = match[1].lower(), match[2]
        rover = None
        if requested is not None:
            if name.casefold() in requested:
                name = requested[name.casefold()]
            elif name[-2:].upper() in {"_F", "_M"} and name[:-2].casefold() in requested:
                rover = "female" if name[-1].upper() == "F" else "male"
                name = requested[name[:-2].casefold()]
            else:
                continue
        key = (language, rover)
        if key in grouped.setdefault(name, {}):
            raise ValueError(f"Duplicate extracted voice: {path.name}")
        grouped[name][key] = path
    return grouped


async def import_voice_sample(root: Path, decoder: Path, asset_version: str, *,
                              names: set[str] | None = None, require_all_languages: bool = True,
                              grouped: dict | None = None, source_context: dict | None = None) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+", asset_version):
        raise ValueError("Invalid asset version")
    source = pinned_source(source_context, asset_version) if source_context is not None else None
    if grouped is None:
        grouped = exported_voices(root, names)
    missing = [f"{language}_{name}" for name in sorted(names or grouped)
               for language in LANGUAGES if not any(key[0] == language for key in grouped.get(name, {}))]
    if not grouped and require_all_languages:
        raise ValueError("No extracted voices")
    for name, languages in grouped.items():
        if require_all_languages and {key[0] for key in languages} != set(LANGUAGES):
            raise ValueError(f"Incomplete language sample: {name}")
    if not grouped:
        return {"voice_references": 0, "tracks": 0, "asset_version": asset_version,
                "missing_voices": missing}
    storage = S3Storage(get_settings())
    await storage.ensure_bucket()
    registration = FileRegistrationService(storage)
    count = 0
    converted = {}
    async with SessionFactory() as session:
        voices = list(await session.scalars(select(VoiceReference).where(
            VoiceReference.file_name.in_(grouped))))
        paths = {voice_package_path(root, path, source_context)
                 for tracks in grouped.values() for path in tracks.values()}
        existing = await verified_references(session, storage, "voice_audio", source, paths) if source else []
        for voice in voices:
            inventory = [{"source_name": path.name, "path": voice_package_path(root, path, source_context),
                          "language": language, "rover": rover}
                         for (language, rover), path in grouped[voice.file_name].items()]
            for (language, rover), path in grouped[voice.file_name].items():
                source_path = (voice_package_path(root, path, source_context) if source
                               else path.relative_to(root).as_posix())
                provenance = publication_source(
                    source_context, {"path": source_path, "kind": "voice", "language": language},
                    [path], voice_name=voice.file_name, rover=rover,
                ) if source else None
                if provenance:
                    provenance["source_files"][0]["package_path"] = source_path
                reusable = next((ref for ref in existing if ref.source_path == source_path
                                 and ref.source_name == path.name
                                 and ref.metadata_json["game_source"]["source_files"] == provenance["source_files"]), None)
                await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": voice.node_id})
                if reusable is not None:
                    ref = await session.scalar(select(FileReference).where(
                        FileReference.owner_node_id == voice.node_id,
                        FileReference.reference_type == "voice_audio",
                        FileReference.source_name == path.name,
                        FileReference.metadata_json["asset_version"].astext == asset_version))
                    if ref is None:
                        ref = await registration.register_reference(
                            session, file_id=reusable.file_id, reference_type="voice_audio",
                            owner_node_id=voice.node_id, source_name=path.name, source_path=source_path)
                    ref.file_id = reusable.file_id
                    ref.source_path = source_path
                    ref.metadata_json = {**reusable.metadata_json, "voice_tracks": inventory,
                                         "voice_name": voice.file_name, "language": language, "rover": rover,
                                         "game_source": provenance,
                                         "voice_inventory_complete": {key[0] for key in grouped[voice.file_name]} == set(LANGUAGES)}
                    count += 1
                    continue
                wav = path.with_suffix(".wav")
                temporary = path.with_suffix(".partial.wav")
                if path not in converted:
                    await asyncio.to_thread(subprocess.run,
                                        [str(decoder.resolve()), "-i", "-o",
                                         str(temporary.resolve()), str(path.resolve())],
                                        check=True, capture_output=True, timeout=120)
                    with wave.open(str(temporary), "rb") as audio:
                        duration = audio.getnframes() / audio.getframerate()
                        if duration <= 0:
                            raise ValueError(f"Empty decoded voice: {path.name}")
                    temporary.replace(wav)
                    original = await registration.register_file(session, path, "audio_wem", mime_type="audio/x-wem")
                    playable = await registration.register_file(session, wav, "audio_wav", mime_type="audio/wav")
                    variant = await registration.register_variant(session, original.id, playable.id, "voice_pcm_wav")
                    if provenance:
                        variant.metadata_json = {"game_source": provenance}
                    converted[path] = original.id, playable.id, duration
                original_id, playable_id, duration = converted[path]
                ref = await session.scalar(select(FileReference).where(
                    FileReference.owner_node_id == voice.node_id,
                    FileReference.reference_type == "voice_audio",
                    FileReference.source_name == path.name,
                    FileReference.metadata_json["asset_version"].astext == asset_version))
                if ref is None:
                    ref = await registration.register_reference(session, file_id=playable_id,
                                                                reference_type="voice_audio", owner_node_id=voice.node_id,
                                                                source_name=path.name, source_path=path.relative_to(root).as_posix())
                ref.file_id = playable_id
                ref.source_path = source_path
                ref.metadata_json = {"language": language, "rover": rover, "asset_version": asset_version,
                                     "duration_seconds": duration, "original_file_id": original_id,
                                     "voice_name": voice.file_name, "voice_tracks": inventory,
                                     "voice_inventory_complete": {key[0] for key in grouped[voice.file_name]} == set(LANGUAGES),
                                     "publication_file_ids": [original_id, playable_id],
                                     **({"game_source": provenance} if provenance else {})}
                count += 1
            await session.commit()
    return {"voice_references": len(voices), "tracks": count, "asset_version": asset_version,
            "missing_voices": missing}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--decoder", required=True, type=Path)
    parser.add_argument("--asset-version", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(import_voice_sample(
        args.root, args.decoder, args.asset_version))))
