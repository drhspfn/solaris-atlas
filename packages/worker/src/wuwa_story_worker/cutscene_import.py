"""Bounded 3.7 cutscene import from exported assets and source soundtrack banks."""

import asyncio
import re
import struct
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text as sql_text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService


def movie_path(data: bytes) -> str:
    paths = re.findall(rb"filepath://\./(Aki/Movies/[A-Za-z0-9_./-]+\.mp4)\x00", data)
    if len(paths) != 1 or b".." in paths[0].split(b"/"):
        raise ValueError("Expected one safe authored MP4 path")
    return "Client/Content/" + paths[0].decode("ascii")


def bank_media_id(data: bytes) -> int:
    """Only the verified single-source Sound/MusicTrack layout in Wwise bank v172."""
    if len(data) < 12 or data[:4] != b"BKHD" or struct.unpack_from("<I", data, 8)[0] != 172:
        raise ValueError("Unsupported Wwise bank version")
    position = 0
    sources = []
    while position + 8 <= len(data):
        kind, size = data[position : position + 4], struct.unpack_from("<I", data, position + 4)[0]
        payload = data[position + 8 : position + 8 + size]
        if len(payload) != size:
            raise ValueError("Truncated Wwise bank")
        if kind == b"HIRC":
            if len(payload) < 4:
                raise ValueError("Truncated Wwise hierarchy")
            offset = 4
            for _ in range(struct.unpack_from("<I", payload)[0]):
                if offset + 5 > len(payload):
                    raise ValueError("Truncated Wwise object header")
                object_type = payload[offset]
                length = struct.unpack_from("<I", payload, offset + 1)[0]
                obj = payload[offset + 5 : offset + 5 + length]
                if len(obj) != length:
                    raise ValueError("Truncated Wwise object")
                if object_type == 2:
                    source_offset = 9
                elif object_type == 11:
                    if len(obj) < 17:
                        raise ValueError("Truncated Wwise music source")
                    if struct.unpack_from("<I", obj, 4)[0] != 1:
                        raise ValueError("Multiple music sources require authored track timing")
                    source_offset = 13
                else:
                    offset += 5 + length
                    continue
                if len(obj) < source_offset + 4:
                    raise ValueError("Truncated Wwise source")
                if struct.unpack_from("<I", obj, source_offset - 5)[0] != 0x00140001:
                    raise ValueError("Unsupported Wwise source codec")
                sources.append(struct.unpack_from("<I", obj, source_offset)[0])
                offset += 5 + length
        position += 8 + size
    if len(sources) != 1:
        raise ValueError("Only single-source soundtrack banks are supported")
    return sources[0]


async def import_cutscene_sample(
    assets: Path,
    movies: Path,
    audio: Path,
    output: Path,
    decoder: Path,
    ffmpeg: Path,
    asset_version: str,
) -> dict:
    if asset_version != "3.7.0":
        raise ValueError("Cutscene sample bank layout is verified for 3.7.0 only")
    # Explicit scope: Start's two source variants and two shared events, not arbitrary banks.
    stems = ("M0206_Mp4", "M0206_nvzhu_Mp4")
    events = ("play_sequence_music_m0206", "play_sfx_lva_m0206")
    output.mkdir(parents=True, exist_ok=True)
    decoded = []
    for event in events:
        matches = list(assets.rglob(event + ".bnk"))
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one exported bank: {event}")
        media_id = bank_media_id(matches[0].read_bytes())
        inputs = list(audio.rglob(f"{media_id}.wem"))
        if len(inputs) != 1:
            raise ValueError(f"Expected exact soundtrack WEM: {media_id}")
        wav = output / f"{media_id}.wav"
        await asyncio.to_thread(
            subprocess.run,
            [str(decoder.resolve()), "-i", "-o", str(wav.resolve()), str(inputs[0].resolve())],
            check=True,
            capture_output=True,
            timeout=120,
        )
        decoded.append((matches[0], inputs[0], wav))
    prepared = []
    for stem in stems:
        matches = list(assets.rglob(stem + ".uexp"))
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one exported media asset: {stem}")
        authored_path = movie_path(matches[0].read_bytes())
        movie = movies / authored_path
        if not movie.is_file():
            raise FileNotFoundError(authored_path)
        relative = matches[0].relative_to(assets).as_posix()
        if not relative.startswith("Client/Content/"):
            raise ValueError("Asset export must retain its game content path")
        engine = (
            "/Game/" + relative.removeprefix("Client/Content/").removesuffix(".uexp") + "." + stem
        )
        playable = output / (stem + ".mp4")
        temporary = output / (stem + ".partial.mp4")
        await asyncio.to_thread(
            subprocess.run,
            [
                str(ffmpeg.resolve()),
                "-y",
                "-i",
                str(movie.resolve()),
                "-i",
                str(decoded[0][2].resolve()),
                "-i",
                str(decoded[1][2].resolve()),
                "-filter_complex",
                "[1:a][2:a]amix=inputs=2:normalize=0,alimiter=level=false,apad=pad_dur=2[a]",
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(temporary.resolve()),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
        temporary.replace(playable)
        prepared.append((engine, movie, playable))
    storage = S3Storage(get_settings())
    await storage.ensure_bucket()
    service = FileRegistrationService(storage)
    async with SessionFactory() as session:
        for engine, movie, playable in prepared:
            node = await session.scalar(
                select(Node).where(Node.canonical_key == "asset:ue:" + engine)
            )
            if node is None:
                raise ValueError(f"No exact imported media asset node: {engine}")
            await session.execute(sql_text("SELECT pg_advisory_xact_lock(:id)"), {"id": node.id})
            original = await service.register_file(
                session, movie, "video_game", mime_type="video/mp4"
            )
            result = await service.register_file(
                session, playable, "video_mp4", mime_type="video/mp4"
            )
            await service.register_variant(
                session, original.id, result.id, "cutscene_soundtrack_mix"
            )
            source_files = []
            for bank, wem, wav in decoded:
                for path, kind, mime in (
                    (bank, "unknown", "application/octet-stream"),
                    (wem, "audio_wem", "audio/x-wem"),
                    (wav, "audio_wav", "audio/wav"),
                ):
                    source = await service.register_file(session, path, kind, mime_type=mime)
                    source_files.append(source.id)
            reference = await session.scalar(
                select(FileReference).where(
                    FileReference.owner_node_id == node.id,
                    FileReference.reference_type == "cutscene_video",
                    FileReference.metadata_json["asset_version"].astext == asset_version,
                )
            )
            if reference is None:
                reference = await service.register_reference(
                    session,
                    owner_node_id=node.id,
                    file_id=result.id,
                    reference_type="cutscene_video",
                    source_name=movie.name,
                    source_path=engine,
                )
            reference.file_id = result.id
            reference.metadata_json = {
                "asset_version": asset_version,
                "has_audio": True,
                "soundtrack": "music_and_effects",
                "source_file_ids": source_files,
                "audio_start_seconds": [0, 0],
                "scope": "Start",
                "subtitles_included": False,
            }
        await session.commit()
    return {
        "videos": len(prepared),
        "soundtrack": "music_and_effects",
        "asset_version": asset_version,
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("assets", "movies", "audio", "output", "decoder", "ffmpeg"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--asset-version", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(import_cutscene_sample(**vars(args)))))
