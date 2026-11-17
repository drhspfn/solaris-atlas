"""Import only recurring schedules for existing, exactly identified client events."""
import argparse
import asyncio
import re
from datetime import UTC, datetime, timedelta

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


def _calculated(row):
    data = row.source_data or {}
    return not data.get("notice_id") and (data.get("start_estimate") or {}).get("basis") == "declared_recurrence"


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
                if any(row is not previous and not _calculated(row)
                       for row in starts.get((event.id, server, start), [])):
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


def calculated_cycles(events, existing, now):
    """Materialize only the current and next windows of a declared recurrence."""
    if now.tzinfo is None:
        raise ValueError("Current time must have a timezone")
    by_id = {event.id: event for event in events if event.source == CLIENT_SOURCE
             and event.event_kind == "recurring" and event.source_id.startswith("activity:")}
    rows_by_server = {}
    for row in existing:
        if row.event_id in by_id and row.server in SERVER_KEYS:
            rows_by_server.setdefault((row.event_id, row.server), []).append(row)

    planned, obsolete = [], []
    for (event_id, server), rows in rows_by_server.items():
        event = by_id[event_id]
        provenance = event.source_data or {}
        legacy = (provenance.get("legacy_source") == SOURCE and
                  provenance.get("legacy_source_id") == "3:" + event.source_id.removeprefix("activity:"))
        anchors = []
        for row in rows:
            data = row.source_data or {}
            season = row.season if isinstance(row.season, dict) else {}
            cycle = season.get("cycle")
            cycle = cycle if isinstance(cycle, dict) else {}
            weeks, number = cycle.get("weeks"), season.get("start")
            times = data.get("time") or []
            if (data.get("notice_id") or not (data.get("schedule_source") == SOURCE or
                     (legacy and not data.get("schedule_source"))) or
                type(weeks) is not int or not 0 < weeks <= 520 or
                type(number) is not int or number < 1 or not row.starts_at or
                not row.starts_at.tzinfo or not times):
                continue
            try:
                recorded = _datetime(times[0])
            except (TypeError, ValueError):
                continue
            if recorded != row.starts_at or data.get("start_estimate"):
                continue
            anchors.append(row)
        if not anchors:
            continue
        active_anchors = [row for row in anchors if row.starts_at <= now]
        anchor = (max(active_anchors, key=lambda row: row.starts_at) if active_anchors
                  else min(anchors, key=lambda row: row.starts_at))
        explicit = [row for row in rows if not _calculated(row)]
        boundaries = [row.starts_at for row in explicit if row.starts_at and row.starts_at > anchor.starts_at]
        if anchor.ends_at:
            boundaries.append(anchor.ends_at)
        boundary = min(boundaries) if boundaries else None
        step = timedelta(weeks=anchor.season["cycle"]["weeks"])
        explicit_phases = {row.season.get("start") for row in explicit
                           if row is not anchor and isinstance(row.season, dict)
                           and type(row.season.get("start")) is int}
        for row in rows:
            estimate = (row.source_data or {}).get("start_estimate") or {}
            if not _calculated(row) or not row.ends_at or row.ends_at <= now:
                continue
            offset, remainder = divmod(row.starts_at - anchor.starts_at, step) if row.starts_at else (-1, step)
            valid = (estimate.get("occurrence_id") == anchor.id and offset >= 0 and not remainder
                     and row.ends_at == row.starts_at + step
                     and isinstance(row.season, dict)
                     and row.season.get("start") == anchor.season["start"] + offset
                     and row.season.get("start") not in explicit_phases
                     and (not boundary or row.ends_at <= boundary)
                     and (row.source_data or {}).get("schedule_revision") == (anchor.source_data or {}).get("schedule_revision"))
            if not valid:
                obsolete.append(row.id)
        index = max(0, (now - anchor.starts_at) // step)
        for offset in (index, index + 1):
            start = anchor.starts_at + offset * step
            end = start + step
            if end <= now or (boundary and end > boundary):
                continue
            if anchor.season["start"] + offset in explicit_phases:
                continue
            if any(row.starts_at == start and row.id not in obsolete for row in rows):
                continue
            identity = f"cycle:{anchor.id}:{start.astimezone(UTC).isoformat()}"
            planned.append({"event_id": event_id, "server": server,
                "source_occurrence_id": identity, "game_version": None,
                "starts_at": start, "ends_at": end,
                "season": {"start": anchor.season["start"] + offset},
                "source_data": {"start_estimate": {
                    "basis": "declared_recurrence", "occurrence_id": anchor.id,
                    "version": anchor.game_version, "server": server,
                    "weeks": anchor.season["cycle"]["weeks"], "end_is_estimated": True,
                }, "schedule_source": SOURCE,
                "schedule_revision": (anchor.source_data or {}).get("schedule_revision"),
                "recurrence_anchor": anchor.source_occurrence_id}})
    return planned, obsolete


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
        existing = list(await session.scalars(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id.in_([event.id for event in events]))))
    now = datetime.now(UTC)
    calculated, obsolete = calculated_cycles(events, existing, now)
    if apply and obsolete:
        from sqlalchemy import delete

        await session.execute(delete(GameEventOccurrence).where(
            GameEventOccurrence.id.in_(obsolete), GameEventOccurrence.ends_at > now,
            GameEventOccurrence.source_data["start_estimate"]["basis"].as_string() == "declared_recurrence",
            GameEventOccurrence.source_data["notice_id"].astext.is_(None),
        ))
    if apply and calculated:
        await session.execute(insert(GameEventOccurrence).values(calculated).on_conflict_do_nothing(
            constraint="uq_game_event_occurrence"))
    return {"source": SOURCE, "revision": revision, "applied": apply, "occurrences": rows,
            "calculated_cycles": calculated, "superseded_cycle_ids": obsolete}


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
