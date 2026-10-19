"""Persist client-authored events independently of the historical timetable."""

from sqlalchemy import select, text

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.ingestion.game_events import CLIENT_SOURCE, SERVER_KEYS, SOURCE


def client_identity(key: str) -> str:
    return key if key.startswith("gacha:") else f"activity:{key}"


async def store_client_events(session, records: dict, asset_version: str, download_id: str) -> dict:
    if not records:
        raise ValueError("The game client contains no event catalog records")
    # Serialize catalog writes across replicas, but only for the short DB phase.
    await session.execute(text("SELECT pg_advisory_xact_lock(705, 1)"))
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
    version = ".".join(asset_version.split(".")[:2])
    events = []
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
            for field in ("prefab_artwork", "missing_resources"):
                if field in previous:
                    presentation[field] = previous[field]
        event.source, event.source_id = CLIENT_SOURCE, identity
        event.title, event.description = english["title"], english.get(
            "description")
        event.event_kind = record["event_kind"]
        event.banner_path = record.get("banner_path")
        event.rewards = english.get("rewards") or []
        event.source_url = "https://wutheringwaves.kurogames.com/"
        event.source_revision = download_id
        event.source_data = {**provenance,
                             "client": presentation}
        events.append(event)
    if not events:
        raise ValueError("No localized client events could be imported")
    await session.flush()
    occurrences = list(await session.scalars(select(GameEventOccurrence).where(
        GameEventOccurrence.event_id.in_([e.id for e in events]),
        GameEventOccurrence.game_version == version)))
    by_event = {}
    for occurrence in occurrences:
        by_event.setdefault(occurrence.event_id, []).append(occurrence)
    for event in events:
        rows = by_event.get(event.id, [])
        servers = {row.server for row in rows}
        for server in SERVER_KEYS:
            if server not in servers:
                row = GameEventOccurrence(event_id=event.id, server=server,
                                          source_occurrence_id=f"client:{asset_version}", game_version=version,
                                          source_data={})
                session.add(row)
                rows.append(row)
        for row in rows:
            row.banner_path = event.banner_path
            row.source_data = {**(row.source_data or {}),
                               "client": event.source_data["client"]}
    await session.flush()
    return {"events": len(events), "catalog_records": len(records), "unresolved": missing,
            "resource_events": [e.source_id for e in events if any(
                "/Prefabs/" in r["path"] for r in e.source_data["client"].get("resources", []))],
            "artwork_paths": sorted({image["path"] for event in events
                                    for image in event.source_data["client"].get("artwork", [])
                                    if image.get("package_status") != "not_in_client"})}
