"""Freeze bounded English source context at enqueue time for identity and replay."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select, Subquery

from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceRecord
from wuwa_story.db.models.storage import FileReference

MAX_CONTEXT_CHARACTERS = 12_000
MAX_CONTEXT_ROWS = 100


def _source_links(relation: str, release_id: int) -> Subquery:
    return (
        select(Edge.from_node_id.label("parent"), Edge.to_node_id.label("child"))
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(
            RelationType.key == relation,
            Edge.layer == "source",
            select(EdgeEvidence.id)
            .where(EdgeEvidence.edge_id == Edge.id, EdgeEvidence.release_id == release_id)
            .exists(),
        )
        .distinct()
        .subquery()
    )


def _caption_query(
    owner_node_id: int, release_id: int
) -> Select[int, str, int, int, int | None, str, str | None]:
    assets = _source_links("references_asset", release_id)
    variants = _source_links("has_variant", release_id)
    captions = _source_links("uses_caption", release_id)
    observed = select(NodeRevision.node_id).where(NodeRevision.release_id == release_id)
    return (
        select(
            Node.id.label("node_id"),
            Node.canonical_key.label("source_node"),
            NodeRevision.id.label("revision_id"),
            SourceRecord.id.label("source_record_id"),
            SourceRecord.row_index.label("source_index"),
            LocalizationKey.key.label("key"),
            LocalizationValue.content.label("text"),
        )
        .select_from(assets)
        .join(variants, variants.c.child == assets.c.parent)
        .join(captions, captions.c.parent == variants.c.parent)
        .join(Node, Node.id == captions.c.child)
        .join(NodeRevision, NodeRevision.node_id == Node.id)
        .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
        .join(LocalizationKey, LocalizationKey.key == SourceRecord.data["CaptionText"].astext)
        .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
        .join(Locale, Locale.id == LocalizationValue.locale_id)
        .where(
            assets.c.child == owner_node_id,
            assets.c.child.in_(observed),
            variants.c.child.in_(observed),
            variants.c.parent.in_(observed),
            NodeRevision.release_id == release_id,
            SourceRecord.release_id == release_id,
            LocalizationValue.release_id == release_id,
            Locale.code == "en",
            LocalizationValue.status == "resolved_nonempty",
            LocalizationValue.content.is_not(None),
            LocalizationValue.content != "",
        )
        .distinct()
        .order_by(SourceRecord.row_index, Node.id, NodeRevision.id)
        .limit(MAX_CONTEXT_ROWS + 1)
    )


def _bounded(snapshot: dict[str, Any]) -> dict[str, Any]:
    # Count the entire JSON snapshot, including provenance and escaping, not just text.
    while len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True)) > MAX_CONTEXT_CHARACTERS:
        snapshot["truncated"] = True
        if snapshot["rows"]:
            snapshot["rows"].pop()
        elif snapshot.get("context"):
            snapshot["context"] = snapshot["context"][: max(0, len(snapshot["context"]) // 2)]
        else:
            raise ValueError("Video context provenance exceeds the snapshot limit")
    return snapshot


async def video_context_snapshot(
    session: AsyncSession,
    reference: FileReference,
    *,
    context: str | None = None,
) -> dict[str, Any]:
    """Return JSON data to persist in job input and include in its identity hash.

    Cutscenes use source captions exclusively. Generic videos may supply English
    plain text, explicitly marked as caller context rather than authored evidence.
    Caption order is authored source order; no variant timing is inferred.
    """
    snapshot: dict[str, Any] = {
        "language": "en",
        "asset_version": (reference.metadata_json or {}).get("asset_version"),
        "release": None,
        "rows": [],
        "truncated": False,
    }
    if reference.reference_type != "cutscene_video":
        snapshot["basis"] = "caller" if context else "unavailable"
        if context:
            snapshot["context"] = context[:MAX_CONTEXT_CHARACTERS]
            snapshot["truncated"] = len(context) > MAX_CONTEXT_CHARACTERS
        return _bounded(snapshot)

    snapshot["basis"] = "source_captions"
    if not snapshot["asset_version"] or reference.owner_node_id is None:
        return _bounded(snapshot)
    owner = await session.scalar(
        select(Node.id)
        .join(NodeType, NodeType.id == Node.type_id)
        .where(
            Node.id == reference.owner_node_id,
            NodeType.key == "asset_reference",
            Node.canonical_key.startswith("asset:ue:"),
        )
    )
    if owner is None:
        return _bounded(snapshot)
    release = await session.scalar(
        select(GameRelease)
        .where(GameRelease.game_version == snapshot["asset_version"])
        .order_by(
            (GameRelease.id == reference.release_id).desc(),
            GameRelease.sequence.desc(),
        )
        .limit(1)
    )
    if release is None:
        return _bounded(snapshot)
    snapshot["release"] = {
        "id": release.id,
        "game_version": release.game_version,
        "resource_version": release.resource_version,
        "upstream_name": release.upstream_name,
        "upstream_commit": release.upstream_commit,
    }
    result = await session.execute(_caption_query(owner, release.id))
    rows = [dict(row) for row in result.mappings()]
    snapshot["rows"] = rows[:MAX_CONTEXT_ROWS]
    snapshot["truncated"] = len(rows) > MAX_CONTEXT_ROWS
    return _bounded(snapshot)
