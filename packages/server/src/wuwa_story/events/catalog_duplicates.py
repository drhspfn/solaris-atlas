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
    def version_key(version):
        parts = version.split(".") if isinstance(version, str) else []
        return tuple(map(int, parts[:2])) if len(parts) >= 2 and all(p.isdecimal() for p in parts) else None

    def identity(row):
        event_id, _, version, signature, *kind = row
        return event_id, signature, version_key(version) if kind and kind[0] == "recurring" else None

    real = {identity(row) for row in rows if row[1] not in placeholder_ids and row[3] is not None}
    latest = {}
    keep = set()
    for row in rows:
        event_id, occurrence_id, version, signature, *_ = row
        if occurrence_id not in placeholder_ids:
            continue
        if signature is None:
            keep.add(occurrence_id)
            continue
        key_identity = identity(row)
        if key_identity in real:
            continue
        placeholder_identity = (event_id, signature)
        parts = version.split(".") if isinstance(version, str) else []
        key = (tuple(map(int, parts)) if parts and all(p.isdecimal() for p in parts) else (),
               occurrence_id)
        if placeholder_identity not in latest or key > latest[placeholder_identity][0]:
            latest[placeholder_identity] = (key, occurrence_id)
    return keep | {row[1] for row in latest.values()}
