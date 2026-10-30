"""Background public sections, bounded source reads, compact serialization and provenance IDs."""

import re
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select

from wuwa_story.api.routes import nodes
from wuwa_story.api.routes.story import (
    continuity,
    media,
    profiles,
    shared,
    transcripts,
)
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.core import (
    Cutscene,
    CutsceneOrderRevision,
    DialogueLine,
    PlayerChoice,
    QuestAction,
    QuestNode,
)
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.raw import SourceRecord
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.db.repositories import cutscenes as cutscene_repository
from wuwa_story.db.repositories.event_browsing import _PREVIEW_ROLES
from wuwa_story.events.service import browse_events
from wuwa_story.ingestion.character_artwork import character_artwork_scope
from wuwa_story.ingestion.game_events import CLIENT_SOURCE
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.story import service
from wuwa_story.story.cutscenes import summaries as cutscene_summaries
from wuwa_story.urls import record_url

_PRIVATE_FIELDS = {
    "admin",
    "private",
    "credentials",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "permissions",
    "is_admin",
    "can_edit",
    "can_delete",
    "can_import",
}


def _public_json(value: Any) -> Any:
    """Copy public responses, dropping private UI/action extensions recursively."""
    if isinstance(value, dict):
        return {
            key: _public_json(child)
            for key, child in value.items()
            if not isinstance(key, str)
            or (
                key.casefold() not in _PRIVATE_FIELDS
                and not key.casefold().startswith(("admin_", "private_", "_admin", "_private"))
            )
        }
    if isinstance(value, list):
        return [_public_json(child) for child in value]
    return value


def _compact_storage(value, _path=()):
    """Omit designated source bodies, preserving authored runtime parameters."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            source_body = (
                key == "raw_record"
                and _path[-1:] in (("source_record",), ("source",), ("provenance",))
            ) or (
                key == "raw"
                and isinstance(value.get("source"), dict)
                and "source_record_id" in value["source"]
            )
            dialogue_action = (
                _path[-2:] == ("dialogue", "action")
                or _path[-4:] == ("transcript", "lines", "*", "action")
                or _path[-3:] == ("context", "*", "action")
                or _path[-5:] == ("shared_scenes", "*", "dialogue", "*", "action")
            )
            flow_action = _path[-5:] == ("profile", "flow_states", "*", "actions", "*")
            if (
                source_body
                or key == "params"
                and child is not None
                and (dialogue_action or flow_action)
            ):
                continue
            if key == "metadata_json" and _path[-1:] == ("node",) and isinstance(child, dict):
                child = {field: item for field, item in child.items() if field != "data"}
            result[key] = _compact_storage(child, (*_path, key))
        return result
    if isinstance(value, list):
        return [_compact_storage(child, (*_path, "*")) for child in value]
    return value


@contextmanager
def _request_caches(session):
    # A worker may reuse its session for another locale or historical snapshot.
    # Public service label/choice caches are request-scoped, not snapshot-scoped.
    def owned(key):
        return (
            isinstance(key, str)
            and (key.startswith("story_") or key == "wuwa_localized_cache")
            or isinstance(key, tuple)
            and key[:1] == ("quest_type_records",)
        )

    previous = {key: session.info.pop(key) for key in list(session.info) if owned(key)}
    try:
        yield
    finally:
        for key in list(session.info):
            if owned(key):
                del session.info[key]
        session.info.update(previous)


def _label(value, fallback):
    if isinstance(value, dict):
        value = value.get("content") or value.get("inline_text")
    return (shared._display_label(value) or fallback) if isinstance(value, str) else fallback


def _event_version(value):
    # Schedule imports identify a major/minor patch, whereas story snapshots may
    # include a client patch component. Never use string-prefix version matching.
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)*", value):
        return None
    return tuple(int(part) for part in value.split(".")[:2])


def _calendar_payload(payload):
    """Keep every recorded run but only the public calendar's preview artwork."""

    def summary(row):
        return {
            **row,
            "artwork": [
                dict(art) for art in row.get("artwork", []) if art.get("role") in _PREVIEW_ROLES
            ],
        }

    return {
        "event": summary(payload["event"]),
        "occurrences": [summary(run) for run in payload["occurrences"]],
        "unversioned_occurrences": [
            summary(run) for run in payload.get("unversioned_occurrences", [])
        ],
    }


async def _artwork(session, node, kind):
    settings = get_settings()
    rows = await session.execute(
        select(FileReference.source_path, FileReference.file_id, FileLocation.object_key)
        .join(FileObject, FileObject.id == FileReference.file_id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            FileReference.owner_node_id == node.id,
            FileReference.reference_type == "entity_image",
            FileLocation.available.is_(True),
            FileLocation.is_primary.is_(True),
            FileLocation.backend == "s3",
            FileLocation.bucket == settings.s3_bucket,
            FileObject.mime_type.in_(
                ["image/png", "image/jpeg", "image/webp", "image/avif", "image/gif"]
            ),
        )
        .order_by(FileReference.id.desc())
    )
    storage = S3Storage(settings)
    role_id = node.canonical_key.partition(":")[2]
    role_id = int(role_id) if role_id.isdecimal() else None
    artwork = {}
    for path, file_id, object_key in rows:
        if kind == "character" and any(
            character_artwork_scope(field, path, role_id) == "shared_resource"
            for field in ("RoleStand", "RolePortrait", "Icon")
        ):
            continue
        artwork.setdefault(
            path, {"engine_path": path, "file_id": file_id, "url": storage.public_url(object_key)}
        )
    return list(artwork.values())


async def _quest_payload(session, key, locale, version):
    profile = await profiles.quest_profile(
        key, locale=locale, game_version=version, session=session
    )
    # The source profile currently caps its authored node tree at 2000 rows.
    # A successful but incomplete source response must not become durable truth.
    tree = profile.get("tree", [])
    if len(tree) >= 2000:
        quest_id = profile.get("quest", {}).get("game_quest_id")
        if quest_id is None:
            raise ValueError("Cannot validate a saturated quest profile tree")
        count = await session.scalar(
            select(func.count())
            .select_from(QuestNode)
            .where(
                QuestNode.game_quest_id == quest_id,
            )
        )
        if count > len(tree):
            raise ValueError("Source quest profile truncated its authored node tree")
    transcript = await _quest_transcript(session, key, locale, version, include_raw=False)
    return {
        "profile": profile,
        "transcript": transcript,
        "continuity": await continuity.quest_continuity(
            key, locale=locale, game_version=version, session=session
        ),
        "media": await media.quest_media(key, game_version=version, locale=locale, session=session),
    }


async def _quest_transcript(session, key, locale, version, *, include_raw):
    transcript = None
    lines = []
    seen = set()
    while True:
        page = await transcripts.quest_transcript(
            key,
            character=None,
            q=None,
            locale=locale,
            game_version=version,
            limit=2000,
            offset=len(lines),
            focus_line=None,
            include_raw=include_raw,
            session=session,
        )
        if transcript is None:
            transcript = page
        identities = [line["id"] for line in page["lines"]]
        if len(set(identities)) != len(identities) or seen.intersection(identities):
            raise ValueError("Quest transcript pagination did not advance")
        if page.get("offset", len(lines)) != len(lines):
            raise ValueError("Quest transcript returned a different page offset")
        refs = await _raw_refs(session, identities, await service._release_id(session, version))
        seen.update(identities)
        lines.extend(
            {**line, "raw_refs": refs.get(line["id"], _empty_raw_refs())} for line in page["lines"]
        )
        if len(page["lines"]) < 2000:
            break
    return {
        **transcript,
        "lines": lines,
        "lines_returned": len(lines),
        "offset": 0,
        "limit": len(lines),
    }


def _empty_raw_refs():
    return {"source_record_id": None, "action_node_id": None, "choice_source_record_ids": {}}


async def _raw_refs(session, keys, release_id):
    """Batch only IDs, with a cache confined to this source-build request."""
    cache = session.info.setdefault("story_raw_refs", {})
    missing = sorted({key for key in keys if (release_id, key) not in cache})
    if missing:
        releases = await service.releases(session)
        requested = releases.get(release_id)
        # Normalized canonical rows retain their original owning source pointer
        # across imports. Accept that exact pointer from an earlier release, not
        # an arbitrary record for the same node or a future canonical revision.
        source_release_ids = (
            [
                row.id
                for row in releases.values()
                if (service.version_key(row.game_version), row.sequence)
                <= (service.version_key(requested.game_version), requested.sequence)
            ]
            if requested
            else []
        )
    for offset in range(0, len(missing), 500):
        batch = missing[offset : offset + 500]
        for key in batch:
            cache[(release_id, key)] = _empty_raw_refs()
        owners = {}
        for key, line_id, record_id, action_id in await session.execute(
            select(
                Node.canonical_key,
                DialogueLine.node_id,
                DialogueLine.source_record_id,
                case(
                    (
                        QuestAction.source_record_id == DialogueLine.source_record_id,
                        QuestAction.node_id,
                    ),
                    else_=None,
                ),
            )
            .join(DialogueLine, DialogueLine.node_id == Node.id)
            .outerjoin(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
            .join(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
            .where(Node.canonical_key.in_(batch), SourceRecord.release_id.in_(source_release_ids))
        ):
            owners[line_id] = key
            cache[(release_id, key)] = {
                "source_record_id": record_id,
                "action_node_id": action_id,
                "choice_source_record_ids": {},
            }
        if owners:
            for line_id, choice_key, record_id in await session.execute(
                select(Edge.from_node_id, Node.canonical_key, PlayerChoice.source_record_id)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(PlayerChoice, PlayerChoice.node_id == Edge.to_node_id)
                .join(Node, Node.id == PlayerChoice.node_id)
                .join(SourceRecord, SourceRecord.id == PlayerChoice.source_record_id)
                .where(
                    Edge.from_node_id.in_(owners),
                    RelationType.key == "presents_choice",
                    Edge.layer == "source",
                    Edge.basis.in_(
                        [
                            "explicit_reference",
                            "exact_join",
                            "authored_order",
                            "source_array_adjacency",
                        ]
                    ),
                    SourceRecord.release_id.in_(source_release_ids),
                    select(EdgeEvidence.id)
                    .where(
                        EdgeEvidence.edge_id == Edge.id,
                        EdgeEvidence.release_id.in_(source_release_ids),
                    )
                    .exists(),
                )
            ):
                cache[(release_id, owners[line_id])]["choice_source_record_ids"][choice_key] = (
                    record_id
                )
    return {key: cache[(release_id, key)] for key in keys}


async def _event_payload(session, entity_id, locale, snapshot_key):
    selected_version = None if snapshot_key == "current" else _event_version(snapshot_key)
    if snapshot_key != "current" and selected_version is None:
        raise ValueError("Event snapshots require a numeric game version")
    event = await session.get(GameEvent, entity_id)
    if event is None or event.source != CLIENT_SOURCE:
        raise HTTPException(404, "Event not found")
    runs = list(
        await session.execute(
            select(
                GameEventOccurrence.id, GameEventOccurrence.server, GameEventOccurrence.game_version
            )
            .where(GameEventOccurrence.event_id == entity_id)
            .order_by(GameEventOccurrence.starts_at.desc().nullslast(), GameEventOccurrence.id)
        )
    )
    selected, unversioned = [], []
    for occurrence_id, server, version in runs:
        run_version = _event_version(version)
        if snapshot_key != "current" and run_version not in (selected_version, None):
            continue
        response = await browse_events(
            server,
            None,
            None,
            locale,
            session,
            event_id=entity_id,
            occurrence_id=occurrence_id,
        )
        matching = [
            run
            for run in response["events"]
            if run["id"] == entity_id and run["occurrence_id"] == occurrence_id
        ]
        if len(matching) != 1:
            raise ValueError("Event occurrence could not be projected unambiguously")
        run = {**matching[0], "server": server, "game_version": version}
        if snapshot_key != "current" and run_version is None:
            unversioned.append(run)
        else:
            selected.append(run)
    if snapshot_key != "current" and not selected:
        raise HTTPException(404, "No event occurrence recorded for this version")
    # With no runs there is no server or historical version to invent.
    summary = {
        "id": event.id,
        "title": (selected[0].get("title") if selected else None) or event.title,
        "slug": event.slug,
        "href": record_url(event, "events"),
        "kind": event.event_kind,
        "description": event.description,
        "source_url": event.source_url,
    }
    # Capture the source LEFT JOIN fallback even for events with regional runs:
    # another server may have no occurrence. This never selects a regional run.
    response = await browse_events(
        "", None, None, locale, session, event_id=entity_id, occurrence_id=None
    )
    matching = [
        run for run in response["events"] if run["id"] == entity_id and run["occurrence_id"] is None
    ]
    if len(matching) != 1:
        raise ValueError("Undated event could not be projected unambiguously")
    summary.update(matching[0], server=None)
    return {"event": summary, "occurrences": selected, "unversioned_occurrences": unversioned}


async def _node_edges(session, entity_id):
    edges = await session.scalars(
        select(Edge)
        .where(
            or_(Edge.from_node_id == entity_id, Edge.to_node_id == entity_id),
        )
        .order_by(Edge.id)
    )
    # Reuse the exact public edge schema, without the HTTP endpoint's page cap.
    return [nodes.EdgeResponse.model_validate(edge).model_dump() for edge in edges]


async def _node_payload(session, key, locale, entity_id):
    detail = (await nodes.node_detail(key, session=session)).model_dump()
    related = None
    results = []
    seen_pages = set()
    while True:
        page = await nodes.node_related(
            key,
            direction="both",
            relation=None,
            category=None,
            source_file=None,
            include_evidence=True,
            locale=locale,
            limit=500,
            offset=len(results),
            session=session,
        )
        if related is None:
            related = page
        signature = repr(page["results"])
        if page["results"] and signature in seen_pages:
            raise ValueError("Related-node pagination did not advance")
        seen_pages.add(signature)
        results.extend(page["results"])
        if len(page["results"]) < 500:
            break
    return {
        "node": detail,
        "related": {**related, "results": results, "offset": 0, "limit": len(results)},
        "edges": await _node_edges(session, entity_id),
        "source_record": await nodes.node_source_record(key, session=session),
    }


async def _cutscene_catalog(session, entity_id, locale, version):
    release_id = await service._release_id(session, version) if version else None
    row = (
        await session.execute(
            cutscene_repository.archive_query(release_id, "", False).where(
                Cutscene.node_id == entity_id
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Cutscene not observed in this snapshot")
    revision = await session.scalar(
        select(CutsceneOrderRevision.revision).where(CutsceneOrderRevision.id == 1)
    )
    if revision is None:
        raise ValueError("Cutscene order revision has not been published")
    playable = await session.scalar(
        select(
            cutscene_repository.scene_query(release_id, "", True)
            .where(Cutscene.node_id == entity_id)
            .exists()
        )
    )
    containing_release = release_id if release_id is not None else row.latest_release_id
    item = (await cutscene_summaries(session, [row], containing_release, locale))[0]
    item = {
        **item,
        "first_observed_game_version": row.first_observed_game_version,
        "ordering_basis": row.ordering_basis or "fallback",
        "game_version": version if version else row.latest_game_version,
    }
    return {
        "item": item,
        "cursor_values": [
            revision,
            row.cursor_version_0,
            row.cursor_version_1,
            row.cursor_version_2,
            row.cursor_index,
            row.cg_name,
            row.node_id,
        ],
        "playable": bool(playable),
        "release_id": release_id,
        "imported_game_versions": await cutscene_repository.imported_versions(session),
    }
