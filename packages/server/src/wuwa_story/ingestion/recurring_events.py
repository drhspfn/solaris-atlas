"""Import only recurring schedules for existing, exactly identified client events."""
import argparse
import asyncio
import re

import httpx
from sqlalchemy import or_, select, text
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
    """Plan community updates by stable upstream phase, retaining old database keys."""
    indexed = {event.source_id: event for event in events if event.source == CLIENT_SOURCE
               and event.event_kind == "recurring"}
    phases = {}
    starts = {}
    for row in existing:
        phase = row.source_occurrence_id.partition("@")[0]
        if phase.isdecimal():
            phases.setdefault((row.event_id, row.server, phase), []).append(row)
        if row.starts_at:
            starts.setdefault((row.event_id, row.server, row.starts_at), []).append(row)
    seen = set()
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
                phase_key = (event.id, server, occurrence)
                candidates = phases.get(phase_key, [])
                if phase_key in seen or len(candidates) > 1:
                    continue
                seen.add(phase_key)
                previous = candidates[0] if candidates else None
                previous_data = (previous.source_data or {}) if previous else {}
                if previous and (previous_data.get("notice_id") or previous_data.get("schedule_source") not in (None, SOURCE)
                    or (not previous_data.get("schedule_source") and not legacy)):
                    continue
                if any(row is not previous for row in starts.get((event.id, server, start), [])):
                    continue
                identity = previous.source_occurrence_id if previous else occurrence
                client = provenance.get("client") or {}
                asset_version = client.get("asset_version") or ""
                snapshot = ({"client": client} if not previous_data.get("client") and version
                            and asset_version.startswith(version + ".") else {})
                data = {**previous_data, "time": interval, "versions": versions,
                        "schedule_source": SOURCE, "schedule_revision": revision,
                        "schedule_url": f"{SOURCE_RAW_ROOT}/{revision}/events.json", **snapshot}
                if (previous and previous.starts_at == start and getattr(previous, "ends_at", None) == end
                    and getattr(previous, "game_version", None) == version
                    and getattr(previous, "season", None) == item.get("season")
                    and previous_data.get("versions") == versions):
                    continue
                rows.append({"event_id": event.id, "source_occurrence_id": identity,
                    "server": server, "game_version": version, "starts_at": start, "ends_at": end,
                    "season": item.get("season"), "source_data": data})
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
    query = select(GameEventOccurrence).where(GameEventOccurrence.event_id.in_([event.id for event in events]))
    existing = list(await session.scalars(query.with_for_update() if apply else query))
    rows = recurring_schedule_rows(document, events, existing, revision)
    if apply and rows:
        statement = insert(GameEventOccurrence).values(rows)
        await session.execute(statement.on_conflict_do_update(
            constraint="uq_game_event_occurrence",
            set_={key: getattr(statement.excluded, key) for key in
                  ("game_version", "starts_at", "ends_at", "season", "source_data")},
            where=(GameEventOccurrence.source_data["notice_id"].astext.is_(None)
                   & or_(GameEventOccurrence.source_data["schedule_source"].astext.is_(None),
                         GameEventOccurrence.source_data["schedule_source"].astext == SOURCE))))
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
