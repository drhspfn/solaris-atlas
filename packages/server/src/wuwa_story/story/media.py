"""Group exact authored voice references into public playback payloads."""

import re
from collections import defaultdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.config.settings import get_settings
from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.db.repositories.voice_media import (
    published_audio_event_tracks,
    published_sequence_audio_tracks,
    published_voice_tracks,
)
from wuwa_story.storage.s3 import S3Storage

_SEQUENCE_DATA_ASSET_RE = re.compile(r"DA_([A-Za-z0-9]+)$")


def _sequence_sound_bank(engine_path: str) -> str | None:
    """Derive the canonical Wwise sequence sound bank name for LevelA SequenceDataAssets."""
    stem = engine_path.rsplit("/", 1)[-1].split(".", 1)[0]
    match = _SEQUENCE_DATA_ASSET_RE.fullmatch(stem)
    if not match:
        return None
    return f"play_seq_all_sound_{match[1].lower()}.bnk"


def _audio_event_voice_name(engine_path: str) -> str | None:
    """Map an authored play_vo_* TalkAkEvent asset path to its exact PlotAudio file_name."""
    stem = engine_path.rsplit("/", 1)[-1].split(".", 1)[0]
    if stem.lower().startswith("play_vo_") and len(stem) > len("play_vo_"):
        return "vo_" + stem[len("play_vo_") :]
    return None


async def media_links(
    session: AsyncSession, from_ids: list[int], relations: tuple[str, ...], release_id: int
) -> dict[int, list[dict[str, Any]]]:
    if not from_ids:
        return {}
    rows = await repository.media_link_rows(session, from_ids, relations, release_id)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for owner_id, target_id, target_key, relation, basis, source_file, raw_path in rows:
        grouped[owner_id].append(
            {
                "node_id": target_id,
                "canonical_key": target_key,
                "relation": relation,
                "basis": basis,
                "source": {"file": source_file, "raw_path": raw_path},
            }
        )
    return grouped


def _collect_tracks(rows, storage) -> dict[int, dict[tuple[str | None, str | None], dict]]:
    tracks: dict[int, dict[tuple[str | None, str | None], dict]] = defaultdict(dict)
    for track in rows:
        language = track.language
        if language is not None and language not in ("en", "ja", "ko", "zh"):
            continue
        rover = track.rover
        if rover not in (None, "male", "female"):
            continue
        tracks[track.owner_node_id].setdefault(
            (language, rover),
            {
                "rover": rover,
                "language": language,
                "url": storage.public_url(track.object_key),
                "asset_version": track.asset_version,
                "duration_seconds": track.duration_seconds,
            },
        )
    return tracks


async def dialogue_media(
    session: AsyncSession,
    line_ids: list[int],
    release_id: int | None,
    *,
    action_ids_by_line: dict[int, int] | None = None,
    state_ids_by_line: dict[int, int] | None = None,
    link_loader=media_links,
    settings_factory=get_settings,
    storage_factory=S3Storage,
) -> dict[int, dict[str, Any]]:
    """Return voice, TalkAkEvent, and sequence-backed audio references for talk items."""
    if release_id is None or not line_ids:
        return {}
    links = await link_loader(
        session, line_ids, ("has_voice_reference", "posts_audio_event"), release_id
    )
    action_links: dict[int, list[dict[str, Any]]] = {}
    if action_ids_by_line:
        action_ids = sorted({aid for aid in action_ids_by_line.values() if aid is not None})
        if action_ids:
            action_links = await link_loader(
                session, action_ids, ("plays_sequence_asset",), release_id
            )
    voice_ids = [
        link["node_id"]
        for entries in links.values()
        for link in entries
        if link["relation"] == "has_voice_reference"
    ]
    audio_event_links = [
        link
        for entries in links.values()
        for link in entries
        if link["relation"] == "posts_audio_event" and link["canonical_key"].startswith("asset:ue:")
    ]
    voices = (
        {voice.node_id: voice for voice in await repository.voice_references(session, voice_ids)}
        if voice_ids
        else {}
    )
    event_voice_by_name: dict[str, Any] = {}
    if audio_event_links:
        candidate_names = {
            name
            for link in audio_event_links
            if (name := _audio_event_voice_name(link["canonical_key"].removeprefix("asset:ue:")))
        }
        if candidate_names:
            for voice in await repository.voice_references_by_file_names(session, candidate_names):
                if voice.file_name:
                    event_voice_by_name.setdefault(voice.file_name.casefold(), voice)
    all_voice_ids = list(
        dict.fromkeys([*voice_ids, *(voice.node_id for voice in event_voice_by_name.values())])
    )
    audio_event_node_ids = list(dict.fromkeys(link["node_id"] for link in audio_event_links))
    seq_state_ids = (
        sorted(
            {
                state_ids_by_line[line_id]
                for line_id in line_ids
                if action_ids_by_line
                and action_links.get(action_ids_by_line.get(line_id))
                and state_ids_by_line
                and state_ids_by_line.get(line_id) is not None
            }
        )
        if action_links and state_ids_by_line
        else []
    )
    tracks: dict[int, dict[tuple[str | None, str | None], dict]] = defaultdict(dict)
    event_tracks: dict[int, dict[tuple[str | None, str | None], dict]] = defaultdict(dict)
    sequence_tracks: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if all_voice_ids or audio_event_node_ids or seq_state_ids:
        settings = settings_factory()
        storage = storage_factory(settings)
        if all_voice_ids:
            tracks = _collect_tracks(
                await published_voice_tracks(session, all_voice_ids, settings.s3_bucket), storage
            )
        if audio_event_node_ids:
            event_tracks = _collect_tracks(
                await published_audio_event_tracks(
                    session, audio_event_node_ids, settings.s3_bucket
                ),
                storage,
            )
        if seq_state_ids:
            seen_seq: set[tuple[int, str | None, str | None, str | None]] = set()
            for row in await published_sequence_audio_tracks(
                session, seq_state_ids, release_id, settings.s3_bucket
            ):
                key = (row.owner_node_id, row.event_reference, row.language, row.rover)
                if key in seen_seq:
                    continue
                seen_seq.add(key)
                sequence_tracks[row.owner_node_id].append(
                    {
                        "language": row.language,
                        "rover": row.rover,
                        "url": storage.public_url(row.object_key),
                        "asset_version": row.asset_version,
                        "event_reference": row.event_reference,
                        "source_name": row.source_name,
                    }
                )
    result = {}
    all_line_ids = list(
        dict.fromkeys(
            [
                *links.keys(),
                *(
                    line_id
                    for line_id in line_ids
                    if action_ids_by_line and action_links.get(action_ids_by_line.get(line_id))
                ),
            ]
        )
    )
    for line_id in all_line_ids:
        entries = links.get(line_id, [])
        audio_events = []
        for link in entries:
            if link["relation"] != "posts_audio_event" or not link["canonical_key"].startswith(
                "asset:ue:"
            ):
                continue
            engine_path = link["canonical_key"].removeprefix("asset:ue:")
            resolved_tracks = list(event_tracks.get(link["node_id"], {}).values())
            if not resolved_tracks:
                voice_name = _audio_event_voice_name(engine_path)
                matched_voice = event_voice_by_name.get(voice_name.casefold()) if voice_name else None
                if matched_voice is not None:
                    resolved_tracks = list(tracks.get(matched_voice.node_id, {}).values())
            event_payload = {
                "engine_path": engine_path,
                "reference_kind": "unreal_asset_path",
                "basis": link["basis"],
                "source": link["source"],
            }
            if resolved_tracks:
                event_payload["tracks"] = resolved_tracks
            audio_events.append(event_payload)
        payload: dict[str, Any] = {
            "voice_references": [
                {
                    "canonical_key": link["canonical_key"],
                    "plot_audio_id": voice.plot_audio_id,
                    "file_name": voice.file_name,
                    "reference_kind": "plot_audio_filename",
                    "engine_path": None,
                    "media_asset_linked": voice.media_asset_node_id is not None,
                    "basis": link["basis"],
                    "source": link["source"],
                    "tracks": list(tracks.get(voice.node_id, {}).values()),
                }
                for link in entries
                if link["relation"] == "has_voice_reference"
                if (voice := voices.get(link["node_id"])) is not None
            ],
            "audio_event_paths": audio_events,
        }
        seq_entries = (
            action_links.get(action_ids_by_line.get(line_id), []) if action_ids_by_line else []
        )
        if seq_entries:
            state_id = state_ids_by_line.get(line_id) if state_ids_by_line else None
            state_tracks = sequence_tracks.get(state_id, []) if state_id is not None else []
            payload["sequence_assets"] = [
                {
                    "engine_path": (engine_path := link["canonical_key"].removeprefix("asset:ue:")),
                    "reference_kind": "sequence_data_asset",
                    "expected_sound_bank": _sequence_sound_bank(engine_path),
                    "basis": link["basis"],
                    "source": link["source"],
                    "tracks": state_tracks,
                }
                for link in seq_entries
                if link["canonical_key"].startswith("asset:ue:")
            ]
        result[line_id] = payload
    return result
