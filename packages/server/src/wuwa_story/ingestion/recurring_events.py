"""Import only recurring schedules for existing, exactly identified client events."""
import argparse
import asyncio
import re

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.ingestion.game_events import (
    CLIENT_SOURCE,
    SERVER_KEYS,
    SOURCE,
    SOURCE_COMMIT_URL,
    SOURCE_RAW_ROOT,
    _datetime,
)


def recurring_schedule_rows(document, events, existing, revision):
    """Keep existing runs; only exact recurring client/legacy identities qualify."""
    indexed = {event.source_id: event for event in events if event.source == CLIENT_SOURCE
               and event.event_kind == "recurring"}
    occupied = {(row.event_id, row.server, row.source_occurrence_id) for row in existing}
    starts = {(row.event_id, row.server, row.starts_at) for row in existing if row.starts_at}
    rows = []
    for group in document.get("list", []):
        if str(group.get("id")) != "3":
            continue
        for item in group.get("child", []):
            source = str(item.get("sourceId", ""))
            occurrence = str(item.get("id", ""))
            if not source.isdecimal() or not occurrence.isdecimal():
                continue
            event = indexed.get(f"activity:{source}")
            if not event:
                continue
            provenance = event.source_data or {}
            legacy = provenance.get("legacy_source_id")
            if legacy and (provenance.get("legacy_source") != SOURCE or legacy != f"3:{source}"):
                continue
            versions = item.get("ver") or []
            version = next((str(v) for v in versions if re.fullmatch(r"\d+\.\d+", str(v))), None)
            for server, interval in zip(SERVER_KEYS, item.get("time", []), strict=False):
                if not isinstance(interval, list) or not interval or len(interval) > 2:
                    continue
                try:
                    start = _datetime(interval[0])
                    end = _datetime(interval[1]) if len(interval) > 1 else None
                except (ValueError, TypeError):
                    continue
                if (not start or not start.tzinfo or (len(interval) > 1 and not end)
                    or (end and (not end.tzinfo or end <= start))):
                    continue
                identity = f"{occurrence}@{start.isoformat()}"
                if ((event.id, server, occurrence) in occupied or
                    (event.id, server, identity) in occupied or (event.id, server, start) in starts):
                    continue
                occupied.add((event.id, server, identity))
                starts.add((event.id, server, start))
                client = provenance.get("client") or {}
                asset_version = client.get("asset_version") or ""
                snapshot = {"client": client} if version and asset_version.startswith(version + ".") else {}
                rows.append({"event_id": event.id, "source_occurrence_id": identity,
                    "server": server, "game_version": version, "starts_at": start, "ends_at": end,
                    "season": item.get("season"), "source_data": {
                        "time": interval, "versions": versions, "schedule_source": SOURCE,
                        "schedule_revision": revision,
                        "schedule_url": f"{SOURCE_RAW_ROOT}/{revision}/events.json",
                        **snapshot,
                    }})
    return rows


async def import_recurring_schedules(session, *, apply=False):
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.get(SOURCE_COMMIT_URL, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        revision = response.json().get("sha")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Invalid timetable revision")
        response = await client.get(f"{SOURCE_RAW_ROOT}/{revision}/events.json")
        response.raise_for_status()
        document = response.json()
    if apply:
        await session.execute(text("SELECT pg_advisory_xact_lock(705, 1)"))
    events = list(await session.scalars(select(GameEvent).where(
        GameEvent.source == CLIENT_SOURCE, GameEvent.event_kind == "recurring")))
    existing = list(await session.scalars(select(GameEventOccurrence).where(
        GameEventOccurrence.event_id.in_([event.id for event in events]))))
    rows = recurring_schedule_rows(document, events, existing, revision)
    if apply and rows:
        await session.execute(insert(GameEventOccurrence).values(rows).on_conflict_do_nothing(
            constraint="uq_game_event_occurrence"))
        await session.flush()
    return {"source": SOURCE, "revision": revision, "applied": apply, "occurrences": rows}


async def _main():
    import json

    from wuwa_story.db.session import SessionFactory

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="insert missing occurrences")
    args = parser.parse_args()
    async with SessionFactory() as session:
        result = await import_recurring_schedules(session, apply=args.apply)
        if args.apply:
            await session.commit()
        print(json.dumps(result, default=str, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())
