"""Persist client-authored events independently of the historical timetable."""

import re

from sqlalchemy import and_, or_, select, text

from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
from wuwa_story.ingestion.game_events import CLIENT_SOURCE, SERVER_KEYS, SOURCE


def client_identity(key: str) -> str:
    return key if key.startswith("gacha:") else f"activity:{key}"


def _version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)+", value):
        return None
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 2 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


_ENRICHMENT_FIELDS = {"prefab_artwork", "prefab_spine_resources", "missing_resources", "animation_import",
                      "_capture_provenance"}


def _capture_core(capture):
    return {key: value for key, value in capture.items() if key not in _ENRICHMENT_FIELDS}


def _covers_catalog_capture(row, presentation, version):
    snapshot = (row.source_data or {}).get("client") or {}
    scheduled = bool(getattr(row, "starts_at", None) or getattr(row, "ends_at", None))
    if scheduled:
        return bool(snapshot) and _capture_core(snapshot) == _capture_core(presentation)
    source_id = getattr(row, "source_occurrence_id", "")
    if source_id.startswith("client:"):
        return (source_id == f"client:{presentation['asset_version']}" and
                (not snapshot or _capture_core(snapshot) == _capture_core(presentation)))
    return (getattr(row, "game_version", version) == version and
            (not snapshot or _capture_core(snapshot) == _capture_core(presentation)))


async def store_client_events(session, records: dict, asset_version: str, download_id: str) -> dict:
    if not records:
        raise ValueError("The game client contains no event catalog records")
    # Serialize catalog writes across replicas, but only for the short DB phase.
    await session.execute(text("SELECT pg_advisory_xact_lock(705, 1)"))
    snapshot = await session.get(GameEventSnapshot, asset_version, populate_existing=True)
    if snapshot and snapshot.download_id != download_id:
        raise ValueError("Existing event snapshot belongs to a different download")
    members = list(await session.scalars(select(GameEventSnapshotMember).where(
        GameEventSnapshotMember.version == asset_version
    ).with_for_update())) if snapshot else []
    members_by_id = {member.event_id: member for member in members}
    for member in members:
        capture = member.client_capture
        if capture is not None and (not isinstance(capture, dict)
            or capture.get("asset_version") != asset_version or capture.get("download_id") != download_id):
            raise ValueError("Existing event capture belongs to a different version or download")
    existing = list(await session.scalars(select(GameEvent).where(
        GameEvent.source.in_([CLIENT_SOURCE, SOURCE])).order_by(GameEvent.id).with_for_update()))
    indexed = {
        event.source_id: event for event in existing if event.source == CLIENT_SOURCE}
    legacy = {}
    for event in existing:
        if event.source != SOURCE:
            continue
        group, _, identifier = event.source_id.partition(":")
        legacy.setdefault(f"gacha:{identifier}" if group ==
                          "1" else f"activity:{identifier}", event)
    for key, record in records.items():
        if not record.get("locales", {}).get("en"):
            continue
        event = indexed.get(client_identity(key)) or legacy.get(client_identity(key))
        member = members_by_id.get(event.id) if event else None
        if snapshot and snapshot.complete and member is None:
            raise ValueError("Existing complete event snapshot differs from the imported catalog")
        if member and member.client_capture is not None:
            provenance = member.client_capture.get("_capture_provenance") or {}
            if provenance and (not isinstance(provenance, dict)
                               or provenance.get("source_id") != client_identity(key)):
                raise ValueError("Existing event capture belongs to a different source identity")
            incoming = {**record, "asset_version": asset_version, "download_id": download_id,
                        "schedule_status": "server_controlled"}
            if _capture_core(member.client_capture) != _capture_core(incoming):
                raise ValueError("Existing event capture differs from the imported catalog")
    if snapshot and all(record.get("locales", {}).get("en") for record in records.values()):
        matches = [indexed.get(client_identity(key)) or legacy.get(client_identity(key)) for key in records]
        matched_ids = {event.id for event in matches if event is not None}
        if (not set(members_by_id) <= matched_ids or
            (snapshot.complete and (any(event is None for event in matches)
             or len(matches) != len(members_by_id) or matched_ids != set(members_by_id)))):
            raise ValueError("Existing complete event snapshot differs from the imported catalog")
    version = ".".join(asset_version.split(".")[:2])
    events = []
    presentations = []
    missing = []
    for key, record in records.items():
        identity = client_identity(key)
        english = record.get("locales", {}).get("en")
        # Keep unresolved keys in the report; never invent reader-facing names.
        if not english:
            missing.append({"expected": identity, "title_key": record.get("title_key"),
                            "status": "localization_missing"})
            continue
        event = indexed.get(identity) or legacy.get(identity)
        if event is None:
            event = GameEvent(source=CLIENT_SOURCE, source_id=identity)
            session.add(event)
        presentation = {**record, "asset_version": asset_version, "download_id": download_id,
                        "schedule_status": "server_controlled"}
        previous = (event.source_data or {}).get("client", {})
        provenance = dict(event.source_data or {})
        if event.source == SOURCE:
            provenance.update(legacy_source=event.source, legacy_source_id=event.source_id)
        if previous.get("download_id") == download_id:
            for field in ("prefab_artwork", "prefab_spine_resources", "missing_resources", "animation_import"):
                if field in previous:
                    presentation[field] = previous[field]
        old_version, new_version = _version_key(previous.get("asset_version")), _version_key(asset_version)
        older_import = old_version is not None and new_version is not None and new_version < old_version
        event.source, event.source_id = CLIENT_SOURCE, identity
        if not older_import:
            event.title, event.description = english["title"], english.get("description")
            event.event_kind = record["event_kind"]
            event.banner_path = record.get("banner_path")
            event.rewards = english.get("rewards") or []
            event.source_url = "https://wutheringwaves.kurogames.com/"
            event.source_revision = download_id
            event.source_data = {**provenance, "client": presentation}
        events.append(event)
        presentations.append(presentation)
    if not events:
        raise ValueError("No localized client events could be imported")
    await session.flush()
    if snapshot is None:
        snapshot = GameEventSnapshot(version=asset_version, download_id=download_id, complete=not missing)
        session.add(snapshot)
        await session.flush()
    elif not snapshot.complete and not missing:
        snapshot.complete = True
    for event, presentation in zip(events, presentations, strict=True):
        member = members_by_id.get(event.id)
        capture = {**presentation, "_capture_provenance": {
            "basis": "client_catalog_import", "source_id": event.source_id}}
        if member is None:
            session.add(GameEventSnapshotMember(version=asset_version, event_id=event.id,
                                                client_capture=capture))
        elif member.client_capture is None:
            member.client_capture = capture
    occurrences = list(await session.scalars(select(GameEventOccurrence).where(
        GameEventOccurrence.event_id.in_([e.id for e in events]),
        or_(GameEventOccurrence.game_version == version,
            and_(or_(GameEventOccurrence.starts_at.is_not(None), GameEventOccurrence.ends_at.is_not(None)),
                 GameEventOccurrence.source_data["client"]["asset_version"].as_string() == asset_version,
                 GameEventOccurrence.source_data["client"]["download_id"].as_string() == download_id)))
        .order_by(GameEventOccurrence.id).with_for_update()))
    by_event = {}
    for occurrence in occurrences:
        by_event.setdefault(occurrence.event_id, []).append(occurrence)
    for event, presentation in zip(events, presentations, strict=True):
        rows = by_event.get(event.id, [])
        servers = {row.server for row in rows if _covers_catalog_capture(row, presentation, version)}
        for server in SERVER_KEYS:
            if server not in servers:
                row = GameEventOccurrence(event_id=event.id, server=server,
                                          source_occurrence_id=f"client:{asset_version}", game_version=version,
                                          source_data={})
                session.add(row)
                rows.append(row)
        for row in rows:
            snapshot = (row.source_data or {}).get("client", {})
            # A dated run keeps the client presentation captured for that run.
            # Media discovered later for the same download remains visible via the API.
            if getattr(row, "starts_at", None) or getattr(row, "ends_at", None):
                continue
            if snapshot and (snapshot.get("download_id") != download_id or
                             snapshot.get("asset_version") != asset_version):
                continue
            row.banner_path = presentation.get("banner_path")
            row.source_data = {**(row.source_data or {}),
                               "client": presentation}
    await session.flush()
    return {"events": len(events), "catalog_records": len(records), "unresolved": missing,
            "resource_events": [e.source_id for e in events if any(
                "/Prefabs/" in r["path"] for r in e.source_data["client"].get("resources", []))],
            "artwork_paths": sorted({image["path"] for event in events
                                    for image in event.source_data["client"].get("artwork", [])
                                    if image.get("package_status") != "not_in_client"})}
