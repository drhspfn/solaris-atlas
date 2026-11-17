"""Suppress placeholders only when captured client identities agree."""

import json


def client_signature(data):
    client = (data or {}).get("client") or {}
    title = (client.get("locales") or {}).get("en") or {}
    fields = (client.get("table"), client.get("title_key"),
              client.get("description_key"), title.get("title"))
    if any(not isinstance(value, str) or not value for value in fields):
        return None
    featured = client.get("featured_ids") or []
    if not isinstance(featured, list) or any(type(value) is not int for value in featured):
        return None
    return json.dumps([*fields, featured], ensure_ascii=False, separators=(",", ":"))


def visible_occurrences(rows, placeholder_ids):
    """Rows are (event id, occurrence id, version, captured client signature)."""
    real = {(event_id, signature) for event_id, occurrence_id, _, signature in rows
            if occurrence_id not in placeholder_ids and signature is not None}
    latest = {}
    keep = set()
    for event_id, occurrence_id, version, signature in rows:
        if occurrence_id not in placeholder_ids:
            continue
        if signature is None:
            keep.add(occurrence_id)
            continue
        identity = (event_id, signature)
        if identity in real:
            continue
        parts = version.split(".") if isinstance(version, str) else []
        key = (tuple(map(int, parts)) if parts and all(p.isdecimal() for p in parts) else (),
               occurrence_id)
        if identity not in latest or key > latest[identity][0]:
            latest[identity] = (key, occurrence_id)
    return keep | {row[1] for row in latest.values()}
