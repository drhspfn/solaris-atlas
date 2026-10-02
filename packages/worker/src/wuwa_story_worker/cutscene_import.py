"""Publish exported cutscene assets and a validated playback graph."""

import asyncio
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text as sql_text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.graph import Edge, Node
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.cutscenes import Clip, CutsceneRecipe, PlaybackFlow
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.cutscene_segments import export_segments


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


def confined(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Export path escapes its root")
    return path


async def run_tool(args: list[str], timeout: int = 300):
    return await asyncio.to_thread(
        subprocess.run, args, capture_output=True, timeout=timeout, check=True
    )


def video_duration(ffmpeg: Path, movie: Path) -> tuple[float, bool]:
    probe = subprocess.run(
        [str(ffmpeg.resolve()), "-hide_banner", "-i", str(movie.resolve())],
        capture_output=True,
        timeout=30,
    )
    info = probe.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info)
    if not match or "Video: h264" not in info:
        raise ValueError("Expected a finite browser-compatible H.264 movie")
    if info.count("Audio:") > 1:
        raise ValueError("Multiple embedded audio tracks need explicit language mapping")
    hours, minutes, seconds = map(float, match.groups())
    return hours * 3600 + minutes * 60 + seconds, "Audio:" in info


async def import_cutscene_recipe(
    recipe: Path,
    assets: Path,
    movies: Path,
    audio: Path,
    output: Path,
    decoder: Path,
    ffmpeg: Path,
    analyze_variants: bool = False,
) -> dict:
    spec = CutsceneRecipe.model_validate_json(recipe.read_text(encoding="utf-8"))
    # Separate working directories keep simultaneous publishers from overwriting each other.
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        work = Path(temporary)
        prepared, decoded = [], {}
        for index, video in enumerate(spec.videos):
            engine = video.asset.removeprefix("asset:ue:")
            package, object_name = engine.rsplit(".", 1)
            if package.rsplit("/", 1)[-1] != object_name:
                raise ValueError("Expected an exact Unreal media asset identity")
            exported = confined(
                assets, "Client/Content/" + package.removeprefix("/Game/") + ".uexp"
            )
            movie = confined(movies, movie_path(exported.read_bytes()))
            duration, embedded_audio = await asyncio.to_thread(video_duration, ffmpeg, movie)
            for node in spec.flow.nodes:
                if isinstance(node, Clip) and node.asset == video.asset:
                    if (
                        node.start >= duration
                        or node.end is not None
                        and node.end > duration + 0.05
                    ):
                        raise ValueError("Playback range exceeds its source video")
            command = [str(ffmpeg.resolve()), "-y", "-i", str(movie)]
            filters, streams, source_paths = [], [], []
            if embedded_audio:
                streams.append("[0:a:0]")
            for track_index, track in enumerate(video.soundtrack, 1):
                bank = confined(assets, track.bank)
                if bank not in decoded:
                    media_id = bank_media_id(bank.read_bytes())
                    matches = list(audio.rglob(f"{media_id}.wem"))
                    if len(matches) != 1:
                        raise ValueError(f"Expected exactly one soundtrack WEM: {media_id}")
                    wav = work / f"{media_id}.wav"
                    await run_tool(
                        [str(decoder.resolve()), "-i", "-o", str(wav), str(matches[0].resolve())],
                        120,
                    )
                    decoded[bank] = (bank, matches[0], wav)
                bank_file, wem, wav = decoded[bank]
                source_paths.append((bank_file, wem, wav))
                command.extend(["-i", str(wav)])
                length = (track.end_seconds or duration) - track.start_seconds
                if length <= 0 or track.start_seconds >= duration:
                    raise ValueError("Soundtrack timing exceeds video duration")
                filters.append(
                    f"[{track_index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,volume={track.gain_db}dB,adelay={round(track.start_seconds * 1000)}:all=1[t{track_index}]"
                )
                streams.append(f"[t{track_index}]")
            playable = work / f"{index}.mp4"
            has_audio = bool(streams)
            if streams:
                filters.append(
                    "".join(streams)
                    + f"amix=inputs={len(streams)}:normalize=0,alimiter=level=false,apad=whole_dur={duration}[a]"
                )
                command.extend(
                    [
                        "-filter_complex",
                        ";".join(filters),
                        "-map",
                        "0:v:0",
                        "-map",
                        "[a]",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                    ]
                )
            else:
                command.extend(["-map", "0:v:0"])
            command.extend(
                ["-c:v", "copy", "-t", str(duration), "-movflags", "+faststart", str(playable)]
            )
            await run_tool(command)
            prepared.append((video, movie, playable, source_paths, duration, has_audio))
        analysis, segments = None, []
        if (analyze_variants or spec.compare_variants) and len(prepared) > 1:
            analysis, segments = await asyncio.to_thread(
                export_segments,
                [item[2] for item in prepared],
                ffmpeg.resolve(),
                output / "segments",
            )
            nodes = []
            shared = next((segment for segment in segments if segment["role"] == "shared"), None)
            intro = next((segment for segment in segments if segment["role"] == "intro"), None)
            outro = next((segment for segment in segments if segment["role"] == "outro"), None)
            branches = [segment for segment in segments if segment["role"].startswith("branch-")]
            source_choices = next((node for node in spec.flow.nodes if node.kind == "choice"), None)
            labels = (
                [option.label for option in source_choices.options]
                if source_choices
                else [f"Variant {index + 1}" for index in range(len(prepared))]
            )
            if branches:
                nodes.append(
                    {
                        "id": "choice",
                        "kind": "choice",
                        "prompt": source_choices.prompt if source_choices else "Choose a variant",
                        "options": [
                            {
                                "label": labels[index]
                                if index < len(labels)
                                else f"Variant {index + 1}",
                                "next": segment["id"],
                                "rover": next(
                                    (
                                        option.rover
                                        for option in source_choices.options
                                        if any(
                                            node.id == option.next
                                            and node.kind == "clip"
                                            and node.asset
                                            == spec.videos[segment["source_index"]].asset
                                            for node in spec.flow.nodes
                                        )
                                    ),
                                    None,
                                )
                                if source_choices
                                else None,
                            }
                            for index, segment in enumerate(branches)
                        ],
                    }
                )
            for segment in segments:
                nodes.append(
                    {
                        "id": segment["id"],
                        "kind": "clip",
                        "asset": spec.videos[segment["source_index"]].asset,
                        "segment": segment["id"],
                        "start": 0,
                        "end": (segment["end_frame"] - segment["start_frame"]) / analysis["fps"],
                        "next": "choice"
                        if segment is intro
                        else outro["id"]
                        if segment in branches and outro
                        else None,
                    }
                )
            flow = PlaybackFlow.model_validate(
                {
                    "entry": (shared or intro or {"id": "choice"})["id"],
                    "nodes": nodes,
                    "evidence": f"Automatic frame and PCM comparison {analysis['id']}; source CG variants, not inferred narrative choices.",
                }
            )
            spec = spec.model_copy(update={"flow": flow})
        storage = S3Storage(get_settings())
        await storage.ensure_bucket()
        service = FileRegistrationService(storage)
        async with SessionFactory() as session:
            keys = sorted([spec.cutscene] + [video.asset for video in spec.videos])
            nodes_by_key = {
                node.canonical_key: node
                for node in await session.scalars(select(Node).where(Node.canonical_key.in_(keys)))
            }
            if set(nodes_by_key) != set(keys):
                raise ValueError("Recipe references missing imported game nodes")
            variant_ids = list(
                await session.scalars(
                    select(Edge.to_node_id)
                    .join(RelationType, RelationType.id == Edge.relation_type_id)
                    .where(
                        Edge.from_node_id == nodes_by_key[spec.cutscene].id,
                        RelationType.key == "has_variant",
                        Edge.layer == "source",
                    )
                )
            )
            linked_assets = set(
                await session.scalars(
                    select(Edge.to_node_id)
                    .join(RelationType, RelationType.id == Edge.relation_type_id)
                    .where(
                        Edge.from_node_id.in_(variant_ids),
                        RelationType.key == "references_asset",
                        Edge.layer == "source",
                    )
                )
            )
            if not {nodes_by_key[video.asset].id for video in spec.videos}.issubset(linked_assets):
                raise ValueError("Recipe videos are not authored variants of this cutscene")
            # Deterministic lock order prevents deadlock when recipes share video assets.
            for node_id in sorted(node.id for node in nodes_by_key.values()):
                await session.execute(
                    sql_text("SELECT pg_advisory_xact_lock(:id)"), {"id": node_id}
                )
            playable_files = []
            for video, movie, playable, source_paths, duration, has_audio in prepared:
                original = await service.register_file(
                    session, movie, "video_game", mime_type="video/mp4"
                )
                result = await service.register_file(
                    session, playable, "video_mp4", mime_type="video/mp4"
                )
                playable_files.append(result)
                await service.register_variant(
                    session, original.id, result.id, "cutscene_soundtrack_mix"
                )
                source_files = []
                for bank, wem, wav in source_paths:
                    for path, kind, mime in (
                        (bank, "unknown", "application/octet-stream"),
                        (wem, "audio_wem", "audio/x-wem"),
                        (wav, "audio_wav", "audio/wav"),
                    ):
                        source = await service.register_file(session, path, kind, mime_type=mime)
                        source_files.append(source.id)
                reference = await publication_reference(
                    session,
                    service,
                    nodes_by_key[video.asset].id,
                    result.id,
                    "cutscene_video",
                    spec.asset_version,
                )
                reference.source_path = video.asset.removeprefix("asset:ue:")
                reference.source_name = movie.name
                reference.metadata_json = {
                    "asset_version": spec.asset_version,
                    "has_audio": has_audio,
                    "soundtrack": "music_and_effects"
                    if video.soundtrack
                    else "embedded"
                    if has_audio
                    else "none",
                    "source_file_ids": source_files,
                    "soundtrack_recipe": [track.model_dump() for track in video.soundtrack],
                    "duration_seconds": duration,
                    "subtitles_included": False,
                }
            if analysis:
                analysis_file = output / "segments" / analysis["id"] / "analysis.json"
                report_file = await service.register_file(
                    session, analysis_file, "raw_json", mime_type="application/json"
                )
                for segment in segments:
                    index = segment["source_index"]
                    file = await service.register_file(
                        session, segment["path"], "video_mp4", mime_type="video/mp4"
                    )
                    await service.register_variant(
                        session, playable_files[index].id, file.id, "cutscene_segment"
                    )
                    reference = await publication_reference(
                        session,
                        service,
                        nodes_by_key[spec.videos[index].asset].id,
                        file.id,
                        "cutscene_segment",
                        spec.asset_version,
                        segment["id"],
                    )
                    reference.metadata_json = {
                        "asset_version": spec.asset_version,
                        "segment_id": segment["id"],
                        "analysis_file_id": report_file.id,
                        "has_audio": prepared[index][5],
                        "soundtrack": "music_and_effects"
                        if spec.videos[index].soundtrack
                        else "embedded"
                        if prepared[index][5]
                        else "none",
                        "start_frame": segment["start_frame"],
                        "end_frame": segment["end_frame"],
                        "fps": analysis["fps"],
                        "subtitles_included": False,
                    }
            flow_file = work / "recipe.json"
            flow_file.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
            registered = await service.register_file(
                session, flow_file, "raw_json", mime_type="application/json"
            )
            reference = await publication_reference(
                session,
                service,
                nodes_by_key[spec.cutscene].id,
                registered.id,
                "cutscene_flow",
                spec.asset_version,
            )
            reference.metadata_json = {
                "asset_version": spec.asset_version,
                "flow": spec.flow.model_dump(),
            }
            await session.commit()
    return {
        "videos": len(spec.videos),
        "cutscene": spec.cutscene,
        "asset_version": spec.asset_version,
    }


async def publication_reference(session, service, owner_id, file_id, kind, version, segment=None):
    reference = await session.scalar(
        select(FileReference).where(
            FileReference.owner_node_id == owner_id,
            FileReference.reference_type == kind,
            FileReference.metadata_json["asset_version"].astext == version,
            FileReference.metadata_json["segment_id"].astext == segment if segment else True,
        )
    )
    if reference is None:
        reference = await service.register_reference(
            session, owner_node_id=owner_id, file_id=file_id, reference_type=kind
        )
    reference.file_id = file_id
    return reference


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("recipe", "assets", "movies", "audio", "output", "decoder", "ffmpeg"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--analyze-variants", action="store_true")
    print(json.dumps(asyncio.run(import_cutscene_recipe(**vars(parser.parse_args())))))
