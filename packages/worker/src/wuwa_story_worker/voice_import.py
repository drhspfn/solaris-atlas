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

from wuwa_story_worker.voice_packages import LANGUAGES


def voice_identity(filename: str) -> tuple[str, str]:
    match = re.fullmatch(r"(en|ja|ko|zh)_(vo_[A-Za-z0-9_]+)\.wem", filename)
    if not match:
        raise ValueError("Expected an exact localized PlotAudio filename")
    return match[1], match[2]


def exported_voices(root: Path, names: set[str] | None = None) -> dict:
    """Resolve exact authored filenames and explicit F/M variants; no fuzzy matching."""
    grouped = {}
    for path in sorted(path for path in root.rglob("*") if path.suffix.casefold() == ".wem"):
        if not re.fullmatch(r"(en|ja|ko|zh)_(vo_[A-Za-z0-9_]+)\.wem", path.stem + ".wem"):
            continue
        language, name = voice_identity(path.stem + ".wem")
        rover = None
        if names is not None and name not in names:
            if name.endswith(("_F", "_M")) and name[:-2] in names:
                rover = "female" if name.endswith("_F") else "male"
                name = name[:-2]
            else:
                continue
        key = (language, rover)
        if key in grouped.setdefault(name, {}):
            raise ValueError(f"Duplicate extracted voice: {path.name}")
        grouped[name][key] = path
    return grouped


async def import_voice_sample(root: Path, decoder: Path, asset_version: str, *,
                              names: set[str] | None = None, require_all_languages: bool = True) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+", asset_version):
        raise ValueError("Invalid asset version")
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
    async with SessionFactory() as session:
        voices = list(await session.scalars(select(VoiceReference).where(
            VoiceReference.file_name.in_(grouped))))
        for voice in voices:
            for (language, rover), path in grouped[voice.file_name].items():
                wav = path.with_suffix(".wav")
                temporary = path.with_suffix(".partial.wav")
                await asyncio.to_thread(subprocess.run,
                                        [str(decoder.resolve()), "-i", "-o",
                                         str(temporary.resolve()), str(path.resolve())],
                                        check=True, capture_output=True, timeout=120)
                with wave.open(str(temporary), "rb") as audio:
                    duration = audio.getnframes() / audio.getframerate()
                    if duration <= 0:
                        raise ValueError(f"Empty decoded voice: {path.name}")
                temporary.replace(wav)
                await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": voice.node_id})
                original = await registration.register_file(session, path, "audio_wem", mime_type="audio/x-wem")
                playable = await registration.register_file(session, wav, "audio_wav", mime_type="audio/wav")
                await registration.register_variant(session, original.id, playable.id, "voice_pcm_wav")
                ref = await session.scalar(select(FileReference).where(
                    FileReference.owner_node_id == voice.node_id,
                    FileReference.reference_type == "voice_audio",
                    FileReference.source_name == path.name,
                    FileReference.metadata_json["asset_version"].astext == asset_version))
                if ref is None:
                    ref = await registration.register_reference(session, file_id=playable.id,
                                                                reference_type="voice_audio", owner_node_id=voice.node_id,
                                                                source_name=path.name, source_path=path.relative_to(root).as_posix())
                ref.file_id = playable.id
                ref.metadata_json = {"language": language, "rover": rover, "asset_version": asset_version,
                                     "duration_seconds": duration}
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
