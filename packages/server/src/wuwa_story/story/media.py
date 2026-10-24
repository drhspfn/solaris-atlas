"""Group exact authored voice references into public playback payloads."""

from collections import defaultdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.config.settings import get_settings
from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.db.repositories.voice_media import published_voice_tracks
from wuwa_story.storage.s3 import S3Storage


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


async def dialogue_media(
    session: AsyncSession,
    line_ids: list[int],
    release_id: int | None,
    *,
    link_loader=media_links,
    settings_factory=get_settings,
    storage_factory=S3Storage,
) -> dict[int, dict[str, Any]]:
    """Return only voice/AK references explicitly linked to these talk items."""
    if release_id is None or not line_ids:
        return {}
    links = await link_loader(
        session, line_ids, ("has_voice_reference", "posts_audio_event"), release_id
    )
    voice_ids = [
        link["node_id"]
        for entries in links.values()
        for link in entries
        if link["relation"] == "has_voice_reference"
    ]
    voices = (
        {voice.node_id: voice for voice in await repository.voice_references(session, voice_ids)}
        if voice_ids
        else {}
    )
    tracks: dict[int, dict[tuple[str, str | None], dict]] = defaultdict(dict)
    if voice_ids:
        settings = settings_factory()
        storage = storage_factory(settings)
        for track in await published_voice_tracks(session, voice_ids, settings.s3_bucket):
            language = track.language
            if language in ("en", "ja", "ko", "zh"):
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
    result = {}
    for line_id, entries in links.items():
        result[line_id] = {
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
            "audio_event_paths": [
                {
                    "engine_path": link["canonical_key"].removeprefix("asset:ue:"),
                    "reference_kind": "unreal_asset_path",
                    "basis": link["basis"],
                    "source": link["source"],
                }
                for link in entries
                if link["relation"] == "posts_audio_event"
                and link["canonical_key"].startswith("asset:ue:")
            ],
        }
    return result
