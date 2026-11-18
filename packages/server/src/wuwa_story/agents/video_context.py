"""Freeze bounded English source context at enqueue time for identity and replay."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select, Subquery

from wuwa_story.db.models.core import DialogueLine, QuestAction, Speaker
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceRecord
from wuwa_story.db.models.storage import FileReference
from wuwa_story.story.cutscene_publications import publication_types

CAPTION_FPS = 30
MAX_CONTEXT_CHARACTERS = 12_000
MAX_CONTEXT_ROWS = 100
MAX_DIALOGUE_ROWS = 40


def scene_text_context(snapshot: dict[str, Any], plan: dict | None, source_file_id: int, release_id: int) -> dict[str, Any] | None:
    """Map pinned source captions to scene time; surrounding dialogue stays untimed."""
    if (snapshot.get("basis") != "source_captions"
            or (snapshot.get("release") or {}).get("id") != release_id):
        return None
    segments = (plan or {}).get("segments") if plan else None
    if segments:
        segments = [segment for segment in segments if segment.get("file_id") == source_file_id]
        if not segments:
            return None
    rows = []
    for row in snapshot.get("rows", []):
        start, end = row.get("start_seconds"), row.get("end_seconds")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start >= end:
            rows.append({**row, "timing_basis": "unavailable"})
            continue
        if not segments:
            rows.append({**row, "timing_basis": "source_caption"})
            continue
        for segment in segments:
            first, last = max(start, segment["source_start"]), min(end, segment["source_end"])
            if first < last:
                offset = segment["scene_start"] - segment["source_start"]
                rows.append({**row, "start_seconds": round(first + offset, 3),
                             "end_seconds": round(last + offset, 3), "timing_basis": "source_caption",
                             "source_start_seconds": start, "source_end_seconds": end})
    return {"basis": "source_captions", "snapshot_id": release_id, "language": snapshot.get("language"),
            "captions": rows, "dialogue_lines": snapshot.get("dialogue_lines", []),
            "dialogue_timing": "surrounding_state_only", "truncated": bool(snapshot.get("truncated")),
            "coverage": "first_selected_source_only" if plan else "source_video"}


def _source_links(relation: str, release_id: int) -> Subquery:
    return (
        select(Edge.from_node_id.label("parent"),
               Edge.to_node_id.label("child"))
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
) -> Select[int, str, int, int, int | None, str, Any, str | None]:
    assets = _source_links("references_asset", release_id)
    variants = _source_links("has_variant", release_id)
    captions = _source_links("uses_caption", release_id)
    observed = select(NodeRevision.node_id).where(
        NodeRevision.release_id == release_id)
    return (
        select(
            Node.id.label("node_id"),
            Node.canonical_key.label("source_node"),
            NodeRevision.id.label("revision_id"),
            SourceRecord.id.label("source_record_id"),
            SourceRecord.row_index.label("source_index"),
            LocalizationKey.key.label("key"),
            SourceRecord.data.label("raw_data"),
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


def _caption_timing(raw_data: Any) -> dict[str, float]:
    if not isinstance(raw_data, dict):
        return {}
    start = raw_data.get("ShowMomentEn")
    duration = raw_data.get("DurationEn")
    if not isinstance(duration, (int, float)) or duration <= 0:
        start = raw_data.get("ShowMoment")
        duration = raw_data.get("Duration")
    if (
        isinstance(start, (int, float))
        and isinstance(duration, (int, float))
        and start >= 0
        and duration > 0
    ):
        return {
            "start_seconds": round(float(start) / CAPTION_FPS, 3),
            "end_seconds": round(float(start + duration) / CAPTION_FPS, 3),
        }
    return {}


async def _dialogue_rows(
    session: AsyncSession, owner_node_id: int, release_id: int
) -> list[dict[str, Any]]:
    assets = _source_links("references_asset", release_id)
    variants = _source_links("has_variant", release_id)
    plays = _source_links("plays_cutscene", release_id)
    observed = select(NodeRevision.node_id).where(
        NodeRevision.release_id == release_id)
    state_ids = (
        select(QuestAction.quest_state_node_id)
        .join(plays, plays.c.parent == QuestAction.node_id)
        .join(variants, variants.c.parent == plays.c.child)
        .join(assets, assets.c.parent == variants.c.child)
        .where(
            assets.c.child == owner_node_id,
            QuestAction.quest_state_node_id.is_not(None),
            QuestAction.node_id.in_(observed),
        )
        .distinct()
    )
    line_val = aliased(LocalizationValue, name="line_val")
    speaker_val = aliased(LocalizationValue, name="speaker_val")
    stmt = (
        select(
            DialogueLine.node_id.label("node_id"),
            QuestAction.quest_state_node_id.label("state_id"),
            DialogueLine.source_index.label("order_index"),
            speaker_val.content.label("speaker"),
            line_val.content.label("text"),
        )
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .join(
            line_val,
            (line_val.key_id == DialogueLine.localization_key_id)
            & (line_val.release_id == release_id)
            & (line_val.status == "resolved_nonempty"),
        )
        .join(Locale, (Locale.id == line_val.locale_id) & (Locale.code == "en"))
        .outerjoin(Speaker, Speaker.node_id == DialogueLine.speaker_node_id)
        .outerjoin(
            speaker_val,
            (speaker_val.key_id == Speaker.name_key_id)
            & (speaker_val.locale_id == Locale.id)
            & (speaker_val.release_id == release_id)
            & (speaker_val.status == "resolved_nonempty"),
        )
        .where(
            QuestAction.quest_state_node_id.in_(state_ids),
            DialogueLine.node_id.in_(observed),
            line_val.content.is_not(None),
            line_val.content != "",
        )
        .order_by(QuestAction.quest_state_node_id, DialogueLine.source_index, DialogueLine.node_id)
        .limit(MAX_DIALOGUE_ROWS)
    )
    rows = (await session.execute(stmt)).mappings()
    return [
        {
            "node_id": row["node_id"],
            "state_id": row["state_id"],
            "order_index": row["order_index"],
            "speaker": row["speaker"],
            "text": row["text"],
        }
        for row in rows
    ]


def _bounded(snapshot: dict[str, Any]) -> dict[str, Any]:
    # Count the entire JSON snapshot, including provenance and escaping, not just text.
    while len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True)) > MAX_CONTEXT_CHARACTERS:
        snapshot["truncated"] = True
        if snapshot.get("dialogue_lines"):
            snapshot["dialogue_lines"].pop()
        elif snapshot["rows"]:
            snapshot["rows"].pop()
        elif snapshot.get("context"):
            snapshot["context"] = snapshot["context"][: max(
                0, len(snapshot["context"]) // 2)]
        else:
            raise ValueError(
                "Video context provenance exceeds the snapshot limit")
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
    if reference.reference_type not in publication_types("cutscene_video"):
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
    rows = []
    for row in result.mappings():
        entry = {k: v for k, v in dict(row).items() if k != "raw_data"}
        entry.update(_caption_timing(row.get("raw_data")))
        rows.append(entry)
    snapshot["rows"] = rows[:MAX_CONTEXT_ROWS]
    snapshot["dialogue_lines"] = await _dialogue_rows(session, owner, release.id)
    snapshot["truncated"] = len(rows) > MAX_CONTEXT_ROWS
    return _bounded(snapshot)
