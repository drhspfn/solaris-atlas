"""Suppress catalog placeholders only within the same exact event identity."""

def visible_occurrences(rows, placeholder_ids):
    """Rows are (event id, occurrence id, catalog version); real runs always win."""
    real_events = {event_id for event_id, occurrence_id, _ in rows
                   if occurrence_id not in placeholder_ids}
    latest = {}
    for event_id, occurrence_id, version in rows:
        if occurrence_id not in placeholder_ids or event_id in real_events:
            continue
        parts = version.split(".") if isinstance(version, str) else []
        key = (tuple(map(int, parts)) if parts and all(p.isdecimal() for p in parts) else (),
               occurrence_id)
        if event_id not in latest or key > latest[event_id][0]:
            latest[event_id] = (key, occurrence_id)
    return {row[1] for row in latest.values()}
