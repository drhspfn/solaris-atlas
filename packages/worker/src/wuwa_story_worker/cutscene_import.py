"""Publish exported cutscene assets and a validated playback graph."""

import asyncio
import json
import logging
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text as sql_text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.graph import Edge, Node
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.storage import FileReference, FileVariant
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.cutscenes import Clip, CutsceneRecipe, PlaybackFlow
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.cutscene_segments import VariantAlignmentError, export_segments
from wuwa_story_worker.publication_provenance import (
    pinned_source,
    publication_source,
    recipe_identity,
    verified_references,
)
from wuwa_story_worker.wwise_banks import bank_media_ids, has_authored_event, render_bank

logger = logging.getLogger(__name__)


async def missing_cutscene_identities(names: set[str], *, source_context: dict,
                                      storage=None, recipe_hashes: dict | None = None) -> set[str]:
    """Return canonical cutscene keys lacking a verified complete publication.

    Safe before extraction for a pinned client/tools plan. When a recipe is already
    available, pass recipe_hashes={cutscene: recipe_identity(spec, analyze_variants)}.
    """
    source = pinned_source(source_context)
    if not names:
        return set()
    storage = storage or S3Storage(get_settings())
    async with SessionFactory() as session:
        refs = await verified_references(session, storage, "cutscene_flow", source, names)
        present = {ref.source_path for ref in refs
                   if ref.metadata_json.get("publication_complete")
                   and (recipe_hashes is None or ref.metadata_json["game_source"].get("recipe_sha256")
                        == recipe_hashes.get(ref.source_path))}
        return names - present


def movie_path(data: bytes) -> str:
    # Count all references before validation; an unsafe second reference must not
    # disappear from the candidate set and make a different movie look unique.
    references = re.findall(rb"filepath:[^\x00]*(?:\x00|$)", data, re.IGNORECASE)
    match = re.fullmatch(
        rb"filepath://\./(Aki/Movies/[A-Za-z0-9_./\\-]+\.mp4)\x00",
        references[0], re.IGNORECASE,
    ) if len(references) == 1 else None
    path = match[1].replace(b"\\", b"/") if match else b""
    if not path or any(part in {b"", b".", b".."} for part in path.split(b"/")):
        raise ValueError("Expected one safe authored MP4 path")
    return "Client/Content/" + path.decode("ascii")


def bank_media_id(data: bytes, selected: int | None = None) -> int:
    """Resolve a v172 source, requiring explicit selection for layered/switch banks."""
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
    if selected is not None:
        if selected not in sources:
            raise ValueError("Selected soundtrack media is not present in the bank")
        return selected
    if len(sources) != 1:
        raise ValueError("Only single-source soundtrack banks are supported")
    return sources[0]


def confined(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Export path escapes its root")
    if path.exists():
        return path
    # Unreal references and raw PAK paths can differ in casing.
    current = root.resolve()
    for component in path.relative_to(current).parts:
        exact = current / component
        if exact.exists():
            current = exact
            continue
        matches = [child for child in current.iterdir() if child.name.casefold() == component.casefold()] if current.is_dir() else []
        if len(matches) > 1:
            raise ValueError("Ambiguous case-insensitive export path")
        current = matches[0] if matches else exact
    current = current.resolve()
    if not current.is_relative_to(root.resolve()):
        raise ValueError("Export path escapes its root")
    return current


async def run_tool(args: list[str], timeout: int = 300):
    try:
        return await asyncio.to_thread(
            subprocess.run, args, capture_output=True, timeout=timeout, check=True
        )
    except subprocess.CalledProcessError as error:
        # Local media tools receive file paths, never credentials. Preserve the
        # diagnostic rather than just storing a command and an exit status.
        detail = b"\n".join(part for part in (error.stdout, error.stderr) if part)
        message = detail.decode("utf-8", errors="replace").strip()[-1200:]
        raise RuntimeError(
            f"{Path(args[0]).name} exited {error.returncode}: {message or 'no diagnostic output'}"
        ) from error


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


def soundtrack_wem(audio: Path, identity: int) -> Path:
    exact = audio / f"Client/Content/Aki/WwiseAudio_Generated/Media/{identity}.wem"
    if exact.is_file():
        return exact
    matches = [path for path in audio.rglob("*")
               if path.is_file() and path.name.casefold() == f"{identity}.wem"]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one soundtrack WEM: {identity}")
    return matches[0]


def available_soundtracks(video, assets, audio, missing_assets=None):
    tracks = []
    for track in video.soundtrack:
        try:
            bank = confined(assets, track.bank)
            data = bank.read_bytes()
            if track.media_id is None and has_authored_event(data, bank.stem):
                # Dependencies belong to the selected event/rover branch. An
                # absent file in an unused branch must not discard this track.
                tracks.append(track)
                continue
            try:
                identities = [bank_media_id(data, track.media_id)]
            except ValueError as error:
                if track.media_id is not None or not any(text in str(error) for text in ("single-source", "Multiple music sources")):
                    raise
                identities = bank_media_ids(data)
            for identity in identities:
                soundtrack_wem(audio, identity)
        except ValueError as error:
            if missing_assets is None:
                raise
            reason = f"{Path(track.bank).name}: {error}"
            if reason not in missing_assets:
                missing_assets.append(reason)
            continue
        tracks.append(track)
    return video.model_copy(update={"soundtrack": tracks})


async def decode_soundtrack(track, assets, audio, work, decoder, decoded):
    bank = confined(assets, track.bank)
    key = (bank, track.media_id, getattr(track, "rover", None))
    if key not in decoded:
        if track.media_id is None and has_authored_event(bank.read_bytes(), bank.stem):
            decoded[key] = await render_bank(bank, audio, work, decoder, getattr(track, "rover", None), run_tool)
            return decoded[key]
        try:
            identity = bank_media_id(bank.read_bytes(), track.media_id)
        except ValueError as error:
            if track.media_id is not None or not any(text in str(error) for text in ("single-source", "Multiple music sources")):
                raise
            decoded[key] = await render_bank(bank, audio, work, decoder, getattr(track, "rover", None), run_tool)
        else:
            wem = soundtrack_wem(audio, identity)
            wav = work / f"{identity}.wav"
            await run_tool([str(decoder.resolve()), "-i", "-o", str(wav), str(wem.resolve())], 120)
            decoded[key] = (wav, [bank, wem])
    return decoded[key]


async def localized_audio_recipe(video, movie, assets, audio, work, decoder, ffmpeg, duration, embedded_audio, asset_version, decoded=None):
    """Keep localized event voices separate from the original movie audio mix."""
    if not any(track.language for track in video.soundtrack):
        return None
    from wuwa_story.ingestion.cutscene_audio import CutsceneAudioRecipe

    work.mkdir(parents=True, exist_ok=True)
    decoded = decoded if decoded is not None else {}
    groups = {}
    for index, track in enumerate(video.soundtrack):
        wav, originals = await decode_soundtrack(track, assets, audio, work, decoder, decoded)
        evidence = []
        for source in originals:
            destination = work / f"source-{index}-{source.name}"
            shutil.copyfile(source, destination)
            evidence.append(destination.name)
        groups.setdefault(track.language, []).append((wav, track, evidence))
    if embedded_audio:
        groups.setdefault(None, [])
    stems = []
    for language, tracks in groups.items():
        command = [str(ffmpeg.resolve()), "-v", "error", "-y"]
        filters, streams, evidence = [], [], []
        input_index = 0
        if language is None and embedded_audio:
            command += ["-i", str(movie)]
            filters.append("[0:a:0]asetpts=PTS-STARTPTS[embedded]")
            streams.append("[embedded]")
            # The original movie is the evidence for its unseparated audio mix.
            original = work / "original.mp4"
            shutil.copyfile(movie, original)
            evidence.append(original.name)
            input_index = 1
        for wav, track, sources in tracks:
            command += ["-i", str(wav)]
            length = min(track.end_seconds or duration, duration) - track.start_seconds
            if length <= 0:
                raise ValueError("Soundtrack timing exceeds video duration")
            filters.append(f"[{input_index}:a]atrim=duration={length},asetpts=PTS-STARTPTS,volume={track.gain_db}dB,adelay={round(track.start_seconds * 1000)}:all=1[t{input_index}]")
            streams.append(f"[t{input_index}]")
            evidence.extend(sources)
            input_index += 1
        filters.append("".join(streams) + f"amix=inputs={len(streams)}:normalize=0,alimiter=level=false,apad=whole_dur={duration}[a]")
        destination = work / f"{language or 'original-mix'}.wav"
        command += ["-filter_complex", ";".join(filters), "-map", "[a]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-t", str(duration), str(destination)]
        await run_tool(command)
        stems.append({"role": "voice" if language else "effects", "language": language,
                      "path": destination.name, "sources": evidence,
                      "evidence": "VideoSound source timing and localized Event bank directory; original embedded audio remains an unseparated mix."})
    return CutsceneAudioRecipe.model_validate({"asset": video.asset, "asset_version": asset_version,
                                              "duration": duration, "stems": stems})


async def import_cutscene_recipe(
    recipe: Path,
    assets: Path,
    movies: Path,
    audio: Path,
    output: Path,
    decoder: Path,
    ffmpeg: Path,
    analyze_variants: bool = False,
    missing_assets: list[str] | None = None,
    source_context: dict | None = None,
) -> dict:
    spec = CutsceneRecipe.model_validate_json(recipe.read_text(encoding="utf-8"))
    requested_spec = spec
    recipe_hash = recipe_identity(spec, analyze_variants)
    if source_context is not None:
        pinned_source(source_context, spec.asset_version)
        missing = await missing_cutscene_identities(
            {spec.cutscene}, source_context=source_context,
            recipe_hashes={spec.cutscene: recipe_hash})
        if not missing:
            return {"videos": len(spec.videos), "cutscene": spec.cutscene,
                    "asset_version": spec.asset_version, "skipped": True}
    spec = spec.model_copy(update={"videos": [available_soundtracks(video, assets, audio, missing_assets)
                                             for video in spec.videos]})
    # Separate working directories keep simultaneous publishers from overwriting each other.
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        work = Path(temporary)
        prepared, decoded, bundles = [], {}, []
        provenance_by_asset = {}
        originals_by_asset = {}
        published_references = {}
        resolved_videos = []
        for video in spec.videos:
            tracks = []
            for track in video.soundtrack:
                try:
                    await decode_soundtrack(track, assets, audio, work, decoder, decoded)
                except ValueError as error:
                    if missing_assets is None:
                        raise
                    reason = f"{Path(track.bank).name}: {error}"
                    if reason not in missing_assets:
                        missing_assets.append(reason)
                else:
                    tracks.append(track)
            resolved_videos.append(video.model_copy(update={"soundtrack": tracks}))
        spec = spec.model_copy(update={"videos": resolved_videos})
        for index, video in enumerate(spec.videos):
            engine = video.asset.removeprefix("asset:ue:")
            package, object_name = engine.rsplit(".", 1)
            if package.rsplit("/", 1)[-1].casefold() != object_name.casefold():
                raise ValueError("Expected an exact Unreal media asset identity")
            exported = confined(
                assets, "Client/Content/" + package.removeprefix("/Game/") + ".uexp"
            )
            movie = confined(movies, movie_path(exported.read_bytes()))
            if source_context is not None:
                originals = [exported, movie]
                header = exported.with_suffix(".uasset")
                if header.is_file():
                    originals.append(header)
                for track in video.soundtrack:
                    bank = confined(assets, track.bank)
                    _, evidence = await decode_soundtrack(track, assets, audio, work, decoder, decoded)
                    originals.extend(path for path in evidence if path.suffix.casefold() in {".wem", ".bnk"})
                provenance_by_asset[video.asset] = publication_source(
                    source_context, {"path": engine, "kind": "video"}, list(dict.fromkeys(originals)),
                    recipe_sha256=recipe_hash, recipe=requested_spec.model_dump(mode="json"),
                    analyze_variants=analyze_variants,
                )
                originals_by_asset[video.asset] = list(dict.fromkeys(originals))
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
            for track_index, track in enumerate([track for track in video.soundtrack if track.language in (None, "en")], 1):
                bank = confined(assets, track.bank)
                wav, evidence = await decode_soundtrack(track, assets, audio, work, decoder, decoded)
                source_paths.extend((bank, path, wav) for path in evidence if path.suffix.casefold() == ".wem")
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
            bundle_root = work / f"audio-{index}"
            bundle = await localized_audio_recipe(video, movie, assets, audio, bundle_root,
                                                   decoder, ffmpeg, duration, embedded_audio, spec.asset_version, decoded)
            if bundle:
                bundles.append((bundle_root, bundle))
        analysis, segments = None, []
        comparison_fallback = None
        if (analyze_variants or spec.compare_variants) and len(prepared) > 1:
            try:
                analysis, segments = await asyncio.to_thread(
                    export_segments,
                    [item[2] for item in prepared],
                    ffmpeg.resolve(),
                    output / "segments",
                )
            except (VariantAlignmentError, TimeoutError, subprocess.TimeoutExpired) as error:
                # Segmentation is a storage optimization, not a prerequisite for
                # playback. Keep the authored flow if its complete sources decode.
                comparison_fallback = "unaligned" if isinstance(error, VariantAlignmentError) else "timeout"
                for item in prepared:
                    for path in (item[1], item[2]):
                        await run_tool([
                            str(ffmpeg.resolve()), "-v", "error", "-xerror",
                            "-threads", "1", "-filter_threads", "1",
                            "-i", str(path), "-f", "null", "-",
                        ], timeout=600)
                logger.warning("cutscene.independent_variants cutscene=%s reason=%s", spec.cutscene, error)
            if analysis is not None:
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
                variant = await service.register_variant(
                    session, original.id, result.id, "cutscene_soundtrack_mix"
                )
                provenance = provenance_by_asset.get(video.asset)
                if provenance:
                    variant.metadata_json = {"game_source": provenance}
                source_files = []
                for path in originals_by_asset.get(video.asset, []):
                    if path == movie:
                        continue
                    source = await service.register_file(
                        session, path, "audio_wem" if path.suffix.casefold() == ".wem" else "unknown",
                        mime_type="application/octet-stream")
                    source_files.append(source.id)
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
                    "original_file_id": original.id,
                    **({"game_source": provenance} if provenance else {}),
                }
                published_references[reference.id] = result.id
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
                    variant = await service.register_variant(
                        session, playable_files[index].id, file.id, "cutscene_segment"
                    )
                    provenance = provenance_by_asset.get(spec.videos[index].asset)
                    if provenance:
                        variant.metadata_json = {"game_source": provenance}
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
                        **({"game_source": provenance} if provenance else {}),
                    }
                    published_references[reference.id] = file.id
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
                **({"comparison_fallback": comparison_fallback} if comparison_fallback else {}),
            }
            published_references[reference.id] = registered.id
            reference.source_path = spec.cutscene
            await session.commit()
        if bundles:
            from wuwa_story_worker.cutscene_audio import publish_audio
            for bundle_root, bundle in bundles:
                bundle_path = bundle_root / "recipe.json"
                bundle_path.write_text(bundle.model_dump_json(), encoding="utf-8")
                await publish_audio(bundle_path, bundle_root, ffmpeg)
        if source_context is not None:
            await complete_cutscene_publication(
                spec, provenance_by_asset, storage,
                published_references=published_references,
                complete=(spec.videos == requested_spec.videos and not missing_assets))
    return {
        "videos": len(spec.videos),
        "cutscene": spec.cutscene,
        "asset_version": spec.asset_version,
    }


def publication_file_ids(reference) -> set[int]:
    """Include audio manifests, stem evidence, silent videos and segment reports."""
    metadata = reference.metadata_json or {}
    ids = {reference.file_id} if reference.file_id else set()
    for key in ("original_file_id", "analysis_file_id"):
        if metadata.get(key):
            ids.add(metadata[key])
    ids.update(metadata.get("source_file_ids", []))
    for track in metadata.get("tracks", []):
        ids.add(track["file_id"])
        ids.update(track.get("source_file_ids", []))
    ids.update(metadata.get("videos", {}).values())
    return ids


async def complete_cutscene_publication(spec, provenance_by_asset, storage, *, complete,
                                        published_references):
    """Only mark complete after all separate stem transactions have succeeded."""
    async with SessionFactory() as session:
        nodes = {node.id: node.canonical_key for node in await session.scalars(
            select(Node).where(Node.canonical_key.in_([spec.cutscene] + [v.asset for v in spec.videos])))}
        for node_id in sorted(nodes):
            await session.execute(sql_text("SELECT pg_advisory_xact_lock(:id)"), {"id": node_id})
        refs = list(await session.scalars(select(FileReference).where(
            FileReference.owner_node_id.in_(nodes),
            FileReference.reference_type.in_(["cutscene_video", "cutscene_segment", "cutscene_audio_bundle", "cutscene_flow"]),
            FileReference.metadata_json["asset_version"].astext == spec.asset_version)))
        localized = {video.asset: {track.language for track in video.soundtrack if track.language}
                     for video in spec.videos if any(track.language for track in video.soundtrack)}
        refs = [ref for ref in refs if ref.id in published_references
                or (ref.reference_type == "cutscene_audio_bundle" and nodes[ref.owner_node_id] in localized)]
        # Another publisher may have changed these rows between the video and stem transactions.
        current = {ref.id: ref.file_id for ref in refs}
        if any(current.get(identity) != file_id for identity, file_id in published_references.items()):
            return
        for asset, languages in localized.items():
            owned = [ref for ref in refs if nodes[ref.owner_node_id] == asset]
            bundle = next((ref for ref in owned if ref.reference_type == "cutscene_audio_bundle"), None)
            segments = {ref.metadata_json["segment_id"] for ref in owned
                        if ref.reference_type == "cutscene_segment"}
            complete = complete and audio_bundle_complete(bundle, languages, segments)
        flow = next(ref for ref in refs if ref.reference_type == "cutscene_flow"
                    and nodes[ref.owner_node_id] == spec.cutscene)
        provenance = next(iter(provenance_by_asset.values()))
        # The flow is the completion record for all its videos, not just the first.
        provenance = {**provenance, "package_path": spec.cutscene,
                      "source_files": [file for value in provenance_by_asset.values() for file in value["source_files"]]}
        ids = set()
        for ref in refs:
            ids.update(publication_file_ids(ref))
            value = provenance_by_asset.get(nodes[ref.owner_node_id], provenance)
            ref.metadata_json = {**ref.metadata_json, "game_source": value}
        for variant in await session.scalars(select(FileVariant).where(
                FileVariant.child_file_id.in_(ids),
                FileVariant.variant_type.in_(["cutscene_soundtrack_mix", "cutscene_segment", "cutscene_silent_video"]))):
            if variant.variant_type == "cutscene_silent_video" or not (variant.metadata_json or {}).get("game_source"):
                variant.metadata_json = {**(variant.metadata_json or {}), "game_source": provenance}
        flow.metadata_json = {**flow.metadata_json, "publication_file_ids": sorted(ids),
                              "publication_references": [{"id": ref.id, "file_id": ref.file_id} for ref in refs],
                              "publication_complete": complete}
        # Verify the closure now too: publication cannot be complete with missing bytes.
        await session.flush()
        verified = await verified_references(session, storage, "cutscene_flow",
                                             pinned_source(provenance), {spec.cutscene})
        if flow not in verified:
            flow.metadata_json = {**flow.metadata_json, "publication_complete": False}
        await session.commit()


def audio_bundle_complete(bundle, languages, segments) -> bool:
    if bundle is None:
        return False
    metadata = bundle.metadata_json or {}
    available = {track.get("language") for track in metadata.get("tracks", [])
                 if track.get("role") == "voice" and track.get("file_id")}
    return (languages.issubset(available)
            and ({"full"} | segments).issubset(metadata.get("videos", {})))


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
