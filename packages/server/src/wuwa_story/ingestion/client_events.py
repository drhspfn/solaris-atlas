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
_DERIVED_FIELDS = {"artwork", "resources", "activity_module", "compiler_checksum", "gacha_pool_proof"}


def _capture_core(capture):
    return {key: value for key, value in capture.items() if key not in _ENRICHMENT_FIELDS}


def _capture_identity(capture):
    return {key: value for key, value in _capture_core(capture).items()
            if key not in _DERIVED_FIELDS and key != "source_checksum"}


def _same_authored_capture(existing, incoming):
    old_checksum, new_checksum = existing.get("source_checksum"), incoming.get("source_checksum")
    return (_capture_identity(existing) == _capture_identity(incoming)
            and (not old_checksum or old_checksum == new_checksum))


def _ordinary_banner_role(source_id, capture):
    if not source_id.startswith("gacha:") or capture.get("table") != "GachaViewInfo":
        return None
    featured = capture.get("featured_ids")
    if capture.get("gacha_type") != 2 or not isinstance(featured, list) or len(featured) != 1:
        return None
    return featured[0] if type(featured[0]) is int and featured[0] > 0 else None


def _ordinary_binding_role(event):
    evidence = getattr(event, "canonical_evidence", None) or {}
    if not isinstance(evidence, dict):
        return None
    reference = evidence.get("reference", "")
    prefix = "GachaViewInfo:type2:role:"
    if evidence.get("basis") in {"game_source", "catalog_identity_policy"} and isinstance(reference, str) and reference.startswith(prefix):
        suffix = reference.removeprefix(prefix)
        return int(suffix) if suffix.isdecimal() else None
    return None


async def _bind_ordinary_banners(session, events, presentations, existing):
    groups = {}
    imported = {event.id for event in events}
    for event, capture in zip(events, presentations, strict=True):
        if role := _ordinary_banner_role(event.source_id, capture):
            groups.setdefault(role, []).append(event)
    # A new patch need not retain the previous view ID. Its historical event is
    # still the canonical catalog subject; the new view is another source record.
    for event in existing:
        if event.id not in imported and event.source == CLIENT_SOURCE:
            capture = (event.source_data or {}).get("client") or {}
            if role := _ordinary_banner_role(event.source_id, capture):
                groups.setdefault(role, []).append(event)
    groups = {role: rows for role, rows in groups.items() if len(rows) > 1}
    if not groups:
        return
    ids = [event.id for rows in groups.values() for event in rows]
    members = list(await session.scalars(select(GameEventSnapshotMember).where(
        GameEventSnapshotMember.event_id.in_(ids))))
    history = {}
    for member in members:
        history.setdefault(member.event_id, []).append(member.client_capture)
    for role, rows in groups.items():
        if any(not history.get(event.id) or any(
            not isinstance(capture, dict) or _ordinary_banner_role(event.source_id, capture) != role
            for capture in history[event.id]) for event in rows):
            continue
        row_ids = {event.id for event in rows}
        parents = {event.canonical_event_id for event in rows if event.canonical_event_id is not None}
        if len(parents) > 1 or (parents and not parents <= row_ids):
            continue
        root = next(iter(parents)) if parents else min(row_ids)
        if any(event.canonical_event_id is not None for event in rows if event.id == root):
            continue
        candidates = {event.id for event in rows if event.id != root and event.canonical_event_id is None}
        if any(event.canonical_event_id in candidates for event in existing):
            continue  # Never turn an existing root with children into a nested alias.
        for event in rows:
            if event.id in candidates:
                event.canonical_event_id = root
                event.canonical_evidence = {"basis": "catalog_identity_policy", "reference": f"GachaViewInfo:type2:role:{role}"}


def _selectable_pool(source_id, capture):
    """Validate compiler-produced proof, never infer parent IDs from view IDs."""
    if (not source_id.startswith("gacha:") or capture.get("table") != "GachaViewInfo"
            or capture.get("gacha_type") not in {7, 8, 9, 10, 13, 14}):
        return None
    proof = capture.get("gacha_pool_proof")
    if not isinstance(proof, dict):
        return None
    view = source_id.removeprefix("gacha:")
    members = proof.get("members")
    parent = proof.get("gacha_id")
    if (not view.isdecimal() or type(parent) is not int or parent <= 0
            or type(proof.get("view_id")) is not int or proof["view_id"] != int(view)
            or not isinstance(members, list) or len(members) < 2
            or any(type(item) is not int or item <= 0 for item in members)
            or members != sorted(set(members)) or int(view) not in members
            or any(not isinstance(proof.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", proof[key])
                   for key in ("pool_checksum", "gacha_checksum", "group_checksum"))):
        return None
    return (parent, capture["gacha_type"], tuple(members), proof["gacha_checksum"], proof["group_checksum"])


def _selectable_binding(event):
    evidence = getattr(event, "canonical_evidence", None)
    if not isinstance(evidence, dict) or evidence.get("basis") != "game_source":
        return None
    reference = evidence.get("reference")
    members = evidence.get("members")
    if (not isinstance(reference, str) or not reference.startswith("GachaPool:")
            or not reference.removeprefix("GachaPool:").isdecimal()
            or evidence.get("gacha_type") not in {7, 8, 9, 10, 13, 14}
            or not isinstance(members, list) or any(type(item) is not int for item in members)):
        return None
    return (int(reference.removeprefix("GachaPool:")), evidence["gacha_type"], tuple(members))


async def _bind_selectable_pools(session, events, presentations, existing):
    groups = {}
    for event, capture in zip(events, presentations, strict=True):
        if proof := _selectable_pool(event.source_id, capture):
            groups.setdefault(proof, []).append(event)
    groups = {proof: rows for proof, rows in groups.items()
              if {int(row.source_id.removeprefix("gacha:")) for row in rows} == set(proof[2])}
    if not groups:
        return
    ids = [row.id for rows in groups.values() for row in rows]
    members = list(await session.scalars(select(GameEventSnapshotMember).where(
        GameEventSnapshotMember.event_id.in_(ids))))
    history = {}
    for member in members:
        history.setdefault(member.event_id, []).append(member.client_capture)
    for proof, rows in groups.items():
        if any(not history.get(row.id) or any(
            not isinstance(capture, dict) or not (old := _selectable_pool(row.source_id, capture))
            or old[:3] != proof[:3]
            for capture in history[row.id]) for row in rows):
            continue
        row_ids = {row.id for row in rows}
        parents = {row.canonical_event_id for row in rows if row.canonical_event_id is not None}
        if len(parents) > 1 or (parents and not parents <= row_ids):
            continue
        if any(row.canonical_event_id is not None and (
            not isinstance(row.canonical_evidence, dict)
            or row.canonical_evidence.get("basis") != "game_source"
            or row.canonical_evidence.get("reference") != f"GachaPool:{proof[0]}"
        ) for row in rows):
            continue
        root = next(iter(parents)) if parents else min(row_ids)
        if any(row.canonical_event_id is not None for row in rows if row.id == root):
            continue
        if any(row.canonical_event_id == root and row.id not in row_ids for row in existing):
            continue
        candidates = {row.id for row in rows if row.id != root and row.canonical_event_id is None}
        if any(row.canonical_event_id in candidates for row in existing):
            continue
        for row in rows:
            if row.id in candidates:
                row.canonical_event_id = root
                row.canonical_evidence = {"basis": "game_source", "reference": f"GachaPool:{proof[0]}",
                                          "gacha_type": proof[1], "members": list(proof[2]),
                                          "gacha_checksum": proof[3], "group_checksum": proof[4]}


def _covers_catalog_capture(row, presentation, version):
    snapshot = (row.source_data or {}).get("client") or {}
    scheduled = bool(getattr(row, "starts_at", None) or getattr(row, "ends_at", None))
    if scheduled:
        return bool(snapshot) and _same_authored_capture(snapshot, presentation)
    source_id = getattr(row, "source_occurrence_id", "")
    if source_id.startswith("client:"):
        return (source_id == f"client:{presentation['asset_version']}" and
                (not snapshot or _same_authored_capture(snapshot, presentation)))
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
    automatic_children = {}
    selectable_children = {}
    for item in existing:
        if role := _ordinary_binding_role(item):
            automatic_children.setdefault(item.canonical_event_id, set()).add(role)
        if binding := _selectable_binding(item):
            selectable_children.setdefault(item.canonical_event_id, set()).add(binding)
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
        if event:
            roles = set(automatic_children.get(event.id, ()))
            if role := _ordinary_binding_role(event):
                roles.add(role)
            if roles and roles != {_ordinary_banner_role(client_identity(key), record)}:
                raise ValueError("Imported source conflicts with an existing canonical banner binding")
            bindings = set(selectable_children.get(event.id, ()))
            if binding := _selectable_binding(event):
                bindings.add(binding)
            proof = _selectable_pool(client_identity(key), record)
            if bindings and (not proof or bindings != {proof[:3]}):
                raise ValueError("Imported source conflicts with an existing selectable pool binding")
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
            if not _same_authored_capture(member.client_capture, incoming):
                raise ValueError("Existing event capture differs from the imported catalog")
            changed = any(member.client_capture.get(field) != incoming.get(field)
                          for field in _DERIVED_FIELDS - {"compiler_checksum"})
            checksum = incoming.get("compiler_checksum")
            if changed and (not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)):
                raise ValueError("Derived capture changes require compiler provenance")
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
        if (previous.get("download_id") == download_id
            and previous.get("resources") == record.get("resources")
            and previous.get("artwork") == record.get("artwork")):
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
        else:
            updated = dict(member.client_capture)
            for field in _DERIVED_FIELDS:
                if field == "compiler_checksum" and not capture.get(field):
                    continue
                if field in capture:
                    updated[field] = capture[field]
                else:
                    updated.pop(field, None)
            if "source_checksum" in capture:
                updated["source_checksum"] = capture["source_checksum"]
            member.client_capture = updated
    await session.flush()
    await _bind_ordinary_banners(session, events, presentations, existing)
    await _bind_selectable_pools(session, events, presentations, existing)
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
            if (row.source_data or {}).get("schedule_source") == "manual":
                continue
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
