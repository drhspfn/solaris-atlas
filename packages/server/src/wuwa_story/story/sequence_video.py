"""Exact occurrence identities and administrator-provided YouTube recordings."""

import hashlib
import json
import re
from collections import Counter
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from wuwa_story.db.models.story import SequenceVideoFallback


def youtube_id(value: str) -> str:
    try:
        url = urlsplit(value.strip())
        if url.scheme != "https" or url.username or url.password or url.port is not None:
            raise ValueError
        host = (url.hostname or "").lower()
        parts = url.path.strip("/").split("/")
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if url.path == "/watch":
                values = parse_qs(url.query).get("v", [])
                video = values[0] if len(values) == 1 else ""
            elif len(parts) == 2 and parts[0] in {"embed", "shorts", "live"}:
                video = parts[1]
            else:
                video = ""
        elif host == "youtu.be" and len(parts) == 1:
            video = parts[0]
        else:
            video = ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            raise ValueError
        return video
    except ValueError:
        raise ValueError("Enter a valid HTTPS YouTube video URL") from None


def occurrence_id(quest: str, version: str, event: dict) -> str:
    source = event.get("source") or {}
    identity = [quest, version, event.get("action"), event.get("flow_state"),
                event.get("action_index"), event.get("reference"), event.get("story_block_id"),
                event.get("interaction_id"), source.get("file"), source.get("raw_path")]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


async def attach_sequence_videos(session, manifest):
    events = [event for event in manifest.get("events", []) if event.get("kind") == "sequence"]
    if not events:
        return manifest
    ids = [occurrence_id(manifest["quest"], manifest["game_version"], event) for event in events]
    counts = Counter(ids)
    unique = [key for key in ids if counts[key] == 1]
    rows = await session.scalars(
        select(SequenceVideoFallback).where(SequenceVideoFallback.occurrence_id.in_(unique))
    ) if unique else []
    recordings = {row.occurrence_id: row for row in rows}
    for event, key in zip(events, ids, strict=True):
        event.pop("occurrence_id", None)
        event.pop("youtube_fallback", None)
        if counts[key] != 1:
            continue
        event["occurrence_id"] = key
        row = recordings.get(key)
        if row:
            event["youtube_fallback"] = {
                "video_id": row.video_id, "start_seconds": row.start_seconds,
                "end_seconds": row.end_seconds,
            }
    return manifest
