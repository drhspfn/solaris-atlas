"""Publish aligned WAV stems without duplicating videos for each voice language."""

import asyncio
import json
import tempfile
import wave
from pathlib import Path

from sqlalchemy import select, text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.cutscene_audio import CutsceneAudioRecipe
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.cutscene_import import confined, publication_reference, run_tool


def validate_stem(path: Path, duration: float) -> None:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() not in (1, 2) or stream.getframerate() != 48000:
            raise ValueError("Expected mono/stereo PCM at 48 kHz")
        actual = stream.getnframes() / stream.getframerate()
        if abs(actual - duration) > 0.05:
            raise ValueError("Stem must cover the complete original movie timeline")


async def publish_audio(recipe: Path, root: Path, ffmpeg: Path) -> dict:
    spec = CutsceneAudioRecipe.model_validate_json(recipe.read_text(encoding="utf-8"))
    storage = S3Storage(get_settings())
    service = FileRegistrationService(storage)
    with tempfile.TemporaryDirectory(dir=recipe.parent) as temporary:
        prepared = []
        for index, stem in enumerate(spec.stems):
            source = confined(root, stem.path)
            validate_stem(source, spec.duration)
            evidence = [confined(root, path) for path in stem.sources]
            if any(not path.is_file() for path in evidence):
                raise ValueError("Missing source evidence for soundtrack")
            output = Path(temporary) / f"{index}.ogg"
            await run_tool(
                [
                    str(ffmpeg),
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "192k",
                    str(output),
                ]
            )
            prepared.append((stem, output, evidence))
        async with SessionFactory() as session:
            node = await session.scalar(select(Node).where(Node.canonical_key == spec.asset))
            if node is None:
                raise ValueError("Audio asset has not been imported")
            await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": node.id})
            video = await session.scalar(
                select(FileReference).where(
                    FileReference.owner_node_id == node.id,
                    FileReference.reference_type == "cutscene_video",
                    FileReference.metadata_json["asset_version"].astext == spec.asset_version,
                )
            )
            if video is None or abs(video.metadata_json["duration_seconds"] - spec.duration) > 0.05:
                raise ValueError("Audio must match an exact published video and its duration")
            tracks = []
            for stem, output, evidence in prepared:
                file = await service.register_file(
                    session, output, "audio_ogg", mime_type="audio/ogg"
                )
                source_ids = []
                for path in evidence:
                    kind = {".wem": "audio_wem", ".wav": "audio_wav"}.get(path.suffix, "unknown")
                    source_file = await service.register_file(
                        session, path, kind, mime_type="application/octet-stream"
                    )
                    source_ids.append(source_file.id)
                tracks.append(
                    {
                        "role": stem.role,
                        "language": stem.language,
                        "file_id": file.id,
                        "source_file_ids": source_ids,
                        "evidence": stem.evidence,
                    }
                )
            manifest = {
                "asset_version": spec.asset_version,
                "duration_seconds": spec.duration,
                "tracks": tracks,
            }
            manifest_path = Path(temporary) / "audio.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            file = await service.register_file(
                session, manifest_path, "raw_json", mime_type="application/json"
            )
            reference = await publication_reference(
                session, service, node.id, file.id, "cutscene_audio_bundle", spec.asset_version
            )
            reference.metadata_json = manifest
            await session.commit()
    return {"asset": spec.asset, "tracks": len(tracks)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("recipe", "root", "ffmpeg"):
        parser.add_argument("--" + name, type=Path, required=True)
    print(json.dumps(asyncio.run(publish_audio(**vars(parser.parse_args())))))
