"""Publish aligned WAV stems without duplicating videos for each voice language."""

import asyncio
import hashlib
import json
import tempfile
import wave
from contextlib import nullcontext
from pathlib import Path

from sqlalchemy import delete, select, text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.storage import FileLocation, FileReference
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.cutscene_audio import CutsceneAudioRecipe
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.cutscene_import import confined, run_tool
from wuwa_story_worker.tooling import asset_workspace


def validate_stem(path: Path, duration: float) -> None:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() not in (1, 2) or stream.getframerate() != 48000:
            raise ValueError("Expected mono/stereo PCM at 48 kHz")
        actual = stream.getnframes() / stream.getframerate()
        if abs(actual - duration) > 0.05:
            raise ValueError("Stem must cover the complete original movie timeline")
        expected = stream.getnframes() * stream.getnchannels() * stream.getsampwidth()
        consumed = 0
        while chunk := stream.readframes(65536):
            consumed += len(chunk)
        if consumed != expected:
            raise ValueError('Truncated PCM soundtrack')


async def _run_logged(args, directory, name):
    (directory / f'{name}.command.json').write_text(json.dumps(args), encoding='utf-8')
    result = None
    try:
        result = await run_tool(args)
        return result
    except Exception as error:
        error.add_note(f'Full cutscene audio subprocess logs: {directory}')
        result = error
        while result.__cause__ is not None:
            result = result.__cause__
        raise
    finally:
        for stream in ('stdout', 'stderr'):
            value = getattr(result, stream, None) or b''
            (directory / f'{name}.{stream}.log').write_bytes(value.encode() if isinstance(value, str) else value)


def _sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


async def _bundle_reference(session, service, owner, version, file_id, manifest, reference_type, cutscene_key=None):
    scope = [FileReference.metadata_json['cutscene_key'].astext == cutscene_key] if cutscene_key is not None else []
    references = list(await session.scalars(select(FileReference).where(
        FileReference.owner_node_id == owner, FileReference.reference_type == reference_type,
        FileReference.metadata_json['asset_version'].astext == version,
        *scope,
    ).order_by(FileReference.id.desc()).with_for_update()))
    reference = references[0] if references else await service.register_reference(
        session, owner_node_id=owner, file_id=file_id, reference_type=reference_type)
    reference.file_id = file_id
    reference.metadata_json = manifest
    if len(references) > 1:
        await session.execute(delete(FileReference).where(
            FileReference.id.in_([item.id for item in references[1:]]),
            FileReference.owner_node_id == owner, FileReference.reference_type == reference_type,
            FileReference.metadata_json['asset_version'].astext == version, *scope))
    return reference


async def publish_audio(recipe: Path, root: Path, ffmpeg: Path, *, session=None,
                        reference_type='cutscene_audio_bundle', video_reference_type='cutscene_video',
                        segment_reference_type='cutscene_segment', cutscene_key=None) -> dict:
    """A supplied session owns commit/rollback; all publication kinds are exact, without fallback."""
    owns_session = session is None
    spec = CutsceneAudioRecipe.model_validate_json(recipe.read_text(encoding="utf-8"))
    if not spec.complete or spec.missing_sources:
        raise ValueError('Incomplete soundtrack source closure cannot replace published audio')
    log_root = asset_workspace() / 'logs' / 'cutscene-audio'
    log_root.mkdir(parents=True, exist_ok=True)
    logs = Path(tempfile.mkdtemp(prefix='publication-', dir=log_root))
    (logs / 'recipe.json').write_text(spec.model_dump_json(indent=2), encoding='utf-8')
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
            await _run_logged(
                [
                    str(ffmpeg),
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-map_metadata",
                    "-1",
                    "-fflags",
                    "+bitexact",
                    "-flags:a",
                    "+bitexact",
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "192k",
                    str(output),
                ], logs, f'stem-{index}'
            )
            if not output.is_file() or not output.stat().st_size:
                raise ValueError('Audio encoder did not produce a nonempty soundtrack')
            prepared.append((stem, output, evidence))
        async with (SessionFactory() if owns_session else nullcontext(session)) as session:
            # SessionFactory disables autoflush; see caller's uncommitted video metadata.
            await session.flush()
            node = await session.scalar(select(Node).where(Node.canonical_key == spec.asset))
            if node is None:
                raise ValueError("Audio asset has not been imported")
            await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": node.id})
            scope = [FileReference.metadata_json['cutscene_key'].astext == cutscene_key] if cutscene_key is not None else []
            video = await session.scalar(
                select(FileReference).where(
                    FileReference.owner_node_id == node.id,
                    FileReference.reference_type == video_reference_type,
                    FileReference.metadata_json["asset_version"].astext == spec.asset_version,
                    *scope,
                ).order_by(FileReference.id.desc()).limit(1)
            )
            if video is None or not video.file_id or abs(video.metadata_json["duration_seconds"] - spec.duration) > 0.05:
                raise ValueError("Audio must match an exact published video and its duration")
            tracks = []
            for stem, output, evidence in prepared:
                file = await service.register_file(
                    session, output, "audio_ogg", mime_type="audio/ogg"
                )
                source_ids = []
                for path in evidence:
                    kind = {".wem": "audio_wem", ".wav": "audio_wav", ".mp4": "video_game"}.get(path.suffix.casefold(), "unknown")
                    source_file = await service.register_file(
                        session, path, kind, mime_type="application/octet-stream"
                    )
                    source_ids.append(source_file.id)
                tracks.append(
                    {
                        "role": stem.role,
                        "language": stem.language,
                        **({"rover": stem.rover} if stem.rover else {}),
                        "file_id": file.id,
                        "source_file_ids": source_ids,
                        "evidence": stem.evidence,
                        'stem_sha256': _sha256(confined(root, stem.path)),
                        'source_hashes': [{'path': path.relative_to(root.resolve()).as_posix(),
                                           'sha256': _sha256(path)}
                                          for path in evidence],
                    }
                )
            silent_videos = {}
            rows = await session.execute(
                select(FileReference, FileLocation.object_key)
                .join(FileLocation, FileLocation.file_id == FileReference.file_id)
                .where(
                    FileReference.owner_node_id == node.id,
                    FileReference.reference_type.in_([video_reference_type, segment_reference_type]),
                    FileReference.metadata_json["asset_version"].astext == spec.asset_version,
                    *scope,
                    FileLocation.backend == "s3",
                    FileLocation.bucket == storage.bucket,
                    FileLocation.is_primary.is_(True),
                    FileLocation.available.is_(True),
                ).order_by(FileReference.id.desc())
            )
            for reference, object_key in rows:
                selector = reference.metadata_json.get('segment_id') or 'full'
                if selector in silent_videos:
                    continue
                source = Path(temporary) / f"video-{reference.id}.mp4"
                with source.open("wb") as stream:
                    async for chunk in storage.get(object_key):
                        stream.write(chunk)
                silent = Path(temporary) / f"silent-{reference.id}.mp4"
                await _run_logged(
                    [
                        str(ffmpeg),
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-map",
                        "0:v:0",
                        "-c:v",
                        "copy",
                        "-an",
                        "-movflags",
                        "+faststart",
                        str(silent),
                    ], logs, f'video-{reference.id}'
                )
                if not silent.is_file() or not silent.stat().st_size:
                    raise ValueError('Silent video encoder did not produce a nonempty video')
                file = await service.register_file(
                    session, silent, "video_mp4", mime_type="video/mp4"
                )
                await service.register_variant(
                    session, reference.file_id, file.id, "cutscene_silent_video"
                )
                silent_videos[selector] = file.id
            if 'full' not in silent_videos:
                raise ValueError('A complete published full video is required for the audio bundle')
            manifest = {
                "asset_version": spec.asset_version,
                "duration_seconds": spec.duration,
                "tracks": tracks,
                "videos": silent_videos,
                **({'cutscene_key': cutscene_key} if cutscene_key is not None else {}),
                'publication_complete': True,
                'audio_recipe_sha256': hashlib.sha256(spec.model_dump_json().encode()).hexdigest(),
                'video_reference': {'id': video.id, 'file_id': video.file_id},
                **({'game_source': video.metadata_json['game_source']} if video.metadata_json.get('game_source') else {}),
            }
            manifest_path = Path(temporary) / "audio.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            file = await service.register_file(
                session, manifest_path, "raw_json", mime_type="application/json"
            )
            manifest['publication_file_ids'] = sorted({file.id, *silent_videos.values(),
                *(track['file_id'] for track in tracks), *(identity for track in tracks for identity in track['source_file_ids'])})
            reference = await _bundle_reference(session, service, node.id, spec.asset_version, file.id, manifest,
                                                reference_type, cutscene_key)
            reference.source_path = spec.asset.removeprefix('asset:ue:')
            await session.flush()
            if owns_session:
                await session.commit()
    return {"asset": spec.asset, "tracks": len(tracks), 'log_directory': str(logs)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("recipe", "root", "ffmpeg"):
        parser.add_argument("--" + name, type=Path, required=True)
    print(json.dumps(asyncio.run(publish_audio(**vars(parser.parse_args())))))
