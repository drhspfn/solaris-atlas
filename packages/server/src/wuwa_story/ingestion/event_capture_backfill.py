"""Backfill only consistent retained captures; dry-run unless explicitly applied."""

import re
from collections import defaultdict

from sqlalchemy import select, text

from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
from wuwa_story.ingestion.client_events import _capture_core
from wuwa_story.ingestion.game_events import CLIENT_SOURCE


def _unambiguous_activity_keys(source_id, capture):
    if source_id.startswith("gacha:"):
        return source_id.removeprefix("gacha:").isdecimal() and capture.get("table") == "GachaViewInfo"
    if not source_id.startswith("activity:") or capture.get("table") != "Activity":
        return False
    identifier = source_id.removeprefix("activity:")
    if not identifier.isdecimal():
        return False
    for field in ("name_key", "title_key", "description_key"):
        value = capture.get(field)
        match = re.fullmatch(r"Activity_(\d+)_(?:Title|Desc)", value) if isinstance(value, str) else None
        if match and match[1] != identifier:
            # Some authored rows intentionally share keys. Skip rather than guess.
            return False
    return True


async def backfill_event_captures(session, version, download_id, *, limit=100, after_event_id=0, apply=False):
    if (not re.fullmatch(r"\d+\.\d+\.\d+", version) or not download_id
        or not 1 <= limit <= 100 or type(after_event_id) is not int or after_event_id < 0):
        raise ValueError("A full version, download ID and limit between 1 and 100 are required")
    if apply:
        await session.execute(text("SELECT pg_advisory_xact_lock(705, 1)"))
    snapshot = await session.get(GameEventSnapshot, version, with_for_update=apply)
    if not snapshot or not snapshot.complete or snapshot.download_id != download_id:
        raise ValueError("A complete snapshot for the exact version and download is required")
    statement = (select(GameEventSnapshotMember, GameEvent.source_id, GameEvent.source_data["client"])
        .join(GameEvent, GameEvent.id == GameEventSnapshotMember.event_id)
        .where(GameEventSnapshotMember.version == version, GameEventSnapshotMember.event_id > after_event_id,
               GameEventSnapshotMember.client_capture.is_(None),
               GameEvent.source == CLIENT_SOURCE)
        .order_by(GameEventSnapshotMember.event_id).limit(limit))
    if apply:
        statement = statement.with_for_update(of=GameEventSnapshotMember)
    members = list((await session.execute(statement)).all())
    ids = [member.event_id for member, _, _ in members]
    occurrences = []
    if ids:
        occurrences = list((await session.execute(
            select(GameEventOccurrence.event_id, GameEventOccurrence.id, GameEventOccurrence.source_data["client"])
            .where(GameEventOccurrence.event_id.in_(ids),
                   GameEventOccurrence.source_data["client"]["asset_version"].as_string() == version)
            .limit(5001)
        )).all())
    if len(occurrences) > 5000:
        raise ValueError("Too many capture candidates for this bounded batch")
    by_event = defaultdict(list)
    for event_id, occurrence_id, capture in occurrences:
        by_event[event_id].append((occurrence_id, capture))
    ready, skipped = [], []
    for member, source_id, current in members:
        candidates = by_event[member.event_id]
        if (not isinstance(current, dict) or current.get("asset_version") != version
            or current.get("download_id") != download_id or not candidates):
            skipped.append({"event_id": member.event_id, "reason": "missing_exact_evidence"})
            continue
        if (any(not isinstance(item, dict) or item.get("download_id") != download_id
                or _capture_core(item) != _capture_core(current) for _, item in candidates)
            or not _unambiguous_activity_keys(source_id, current)):
            skipped.append({"event_id": member.event_id, "reason": "conflicting_or_shared_evidence"})
            continue
        ready.append(member.event_id)
        if apply:
            member.client_capture = {**_capture_core(current), "_capture_provenance": {
                "basis": "consistent_retained_captures", "source_id": source_id,
                "occurrence_ids": sorted(identity for identity, _ in candidates)}}
    if apply:
        await session.flush()
    return {"version": version, "download_id": download_id, "examined": len(members),
            "last_event_id": ids[-1] if ids else None,
            "ready": ready, "skipped": skipped, "applied": len(ready) if apply else 0}
