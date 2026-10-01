"""Quest classification and authored progression from imported source tables."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.shared import _localized, _release_id, _version_key
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.i18n import LocalizationKey
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import get_session

router = APIRouter(tags=["story browsing"])


async def _source_row(session: AsyncSession, node_id: int, release_id: int) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(SourceRecord, SourceFile)
            .join(NodeRevision, NodeRevision.source_record_id == SourceRecord.id)
            .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(NodeRevision.node_id == node_id, NodeRevision.release_id == release_id)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    record, source_file = row
    return {
        "raw": record.data,
        "source": {"file": source_file.logical_source_path, "row": record.row_index,
                   "raw_path": f"$[{record.row_index}]", "source_record_id": record.id},
    }


async def _text(session: AsyncSession, key: str | None, locale: str, release_id: int) -> dict[str, Any] | None:
    if not key:
        return None
    key_id = await session.scalar(select(LocalizationKey.id).where(LocalizationKey.key == key))
    return await _localized(session, key_id, locale, release_id)


async def _table_row(
    session: AsyncSession, release_id: int, path: str, identifier: int
) -> dict[str, Any] | None:
    row = await session.scalar(
        select(SourceRecord)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(SourceFile.release_id == release_id,
               SourceFile.logical_source_path == path,
               SourceRecord.data["Id"].as_integer() == identifier)
        .limit(1)
    )
    return {"raw": row.data, "source": {"file": path, "row": row.row_index,
            "raw_path": f"$[{row.row_index}]", "source_record_id": row.id}} if row else None


@router.get("/quests/{game_quest_id}/continuity")
async def quest_continuity(
    game_quest_id: int,
    locale: str = "en",
    game_version: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Expose observed patch presence, quest category, chapter and authored tree links."""
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == game_quest_id))
    if quest is None:
        raise HTTPException(status_code=404, detail="quest not found")
    release_id = await _release_id(session, game_version)
    if release_id is None:
        raise HTTPException(status_code=404, detail="game version not imported")
    release = await session.get(GameRelease, release_id)
    observations = list(
        (
            await session.execute(
                select(GameRelease.game_version, GameRelease.resource_version,
                       GameRelease.upstream_commit, GameRelease.id)
                .join(NodeRevision, NodeRevision.release_id == GameRelease.id)
                .where(NodeRevision.node_id == quest.node_id)
            )
        ).all()
    )
    observations.sort(key=lambda row: (_version_key(row.game_version), row.id))
    source = await _source_row(session, quest.node_id, release_id)
    data = source["raw"].get("Data", {}) if source else {}
    if isinstance(data, str):
        import json
        data = json.loads(data)
    quest_type_id = data.get("Type") if isinstance(data, dict) else None
    chapter_id = data.get("ChapterId") if isinstance(data, dict) else None
    quest_type = await _table_row(
        session, release_id, "BinData/questtype/questtype.json", quest_type_id
    ) if isinstance(quest_type_id, int) else None
    chapter = await _table_row(
        session, release_id, "BinData/quest_chapter/questchapter.json", chapter_id
    ) if isinstance(chapter_id, int) and chapter_id else None

    prerequisite_rows = list(
        (
            await session.execute(
                select(Edge, Node.canonical_key, Quest.name_key_id, EdgeEvidence.source_file_path,
                       EdgeEvidence.source_raw_path)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(Node, Node.id == Edge.to_node_id)
                .join(Quest, Quest.node_id == Node.id)
                .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
                .where(Edge.from_node_id == quest.node_id,
                       RelationType.key == "requires_quest",
                       EdgeEvidence.release_id == release_id)
                .order_by(Node.canonical_key, EdgeEvidence.id)
            )
        ).all()
    )
    required_by_rows = list(
        (
            await session.execute(
                select(Edge, Node.canonical_key, Quest.name_key_id, EdgeEvidence.source_file_path,
                       EdgeEvidence.source_raw_path)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(Node, Node.id == Edge.from_node_id)
                .join(Quest, Quest.node_id == Node.id)
                .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
                .where(Edge.to_node_id == quest.node_id,
                       RelationType.key == "requires_quest",
                       EdgeEvidence.release_id == release_id)
                .order_by(Node.canonical_key, EdgeEvidence.id)
            )
        ).all()
    )

    membership = list(
        (
            await session.execute(
                select(Node, Edge, EdgeEvidence)
                .join(Edge, Edge.from_node_id == Node.id)
                .join(RelationType, RelationType.id == Edge.relation_type_id)
                .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
                .where(Edge.to_node_id == quest.node_id,
                       RelationType.key == "quest_tree_contains_quest",
                       EdgeEvidence.release_id == release_id)
                .order_by(Node.canonical_key, EdgeEvidence.id)
            )
        ).all()
    )
    tree_nodes = []
    seen_nodes = set()
    for node, membership_edge, membership_evidence in membership:
        if node.id in seen_nodes:
            continue
        seen_nodes.add(node.id)
        node_source = await _source_row(session, node.id, release_id)
        raw = node_source["raw"] if node_source else {}
        neighbor_rows = list(
            (
                await session.execute(
                    select(RelationType.key, Node.id, Node.canonical_key, EdgeEvidence.source_file_path,
                           EdgeEvidence.source_raw_path)
                    .select_from(Edge)
                    .join(RelationType, RelationType.id == Edge.relation_type_id)
                    .join(Node, Node.id == Edge.to_node_id)
                    .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
                    .where(Edge.from_node_id == node.id,
                           RelationType.key.in_(("quest_tree_predecessor", "quest_tree_next")),
                           EdgeEvidence.release_id == release_id)
                    .order_by(RelationType.key, Node.canonical_key)
                )
            ).all()
        )
        neighbors = []
        for relation, neighbor_id, key, file, path in neighbor_rows:
            neighbor_source = await _source_row(session, neighbor_id, release_id)
            neighbor_raw = neighbor_source["raw"] if neighbor_source else {}
            neighbors.append({
                "relation": relation, "canonical_key": key,
                "quest_ids": neighbor_raw.get("QuestArray", []),
                "name": await _text(session, neighbor_raw.get("Name"), locale, release_id),
                "source": {"file": file, "raw_path": path},
            })
        tree_chapter_id = raw.get("ChapterId")
        tree_chapter = await _table_row(
            session, release_id, "BinData/QuestTree/questtreechapter.json", tree_chapter_id
        ) if isinstance(tree_chapter_id, int) else None
        tree_nodes.append({
            "canonical_key": node.canonical_key,
            "node_type_id": raw.get("NodeType"), "quest_type_id": raw.get("QuestType"),
            "chapter_id": tree_chapter_id, "quest_array": raw.get("QuestArray", []),
            "tree_chapter_name": await _text(
                session, tree_chapter["raw"].get("Name"), locale, release_id
            ) if tree_chapter else None,
            "name": await _text(session, raw.get("Name"), locale, release_id),
            "chapter_name": await _text(session, raw.get("QuestChapterName"), locale, release_id),
            "summary": await _text(session, raw.get("Summary"), locale, release_id),
            "neighbors": neighbors,
            "membership_source": {"file": membership_evidence.source_file_path,
                                  "raw_path": membership_evidence.source_raw_path,
                                  "basis": membership_edge.metadata_json.get("source_basis")},
            "source": node_source["source"] if node_source else None,
        })
    return {
        "quest": f"quest:{game_quest_id}",
        "selected_game_version": release.game_version if release else None,
        "present_in_selected_snapshot": source is not None,
        "observed_versions": [
            {"game_version": version, "resource_version": resource,
             "upstream_commit": commit, "release_id": observed_id}
            for version, resource, commit, observed_id in observations
        ],
        "first_observed_game_version": observations[0].game_version if observations else None,
        "first_observed_meaning": "Earliest imported snapshot containing this quest; not necessarily its debut patch.",
        "quest_type": {
            "id": quest_type_id,
            "name": await _text(session, quest_type["raw"].get("QuestTypeName"), locale, release_id)
            if quest_type else None,
            "source": quest_type["source"] if quest_type else None,
        },
        "quest_chapter": {
            "id": chapter_id,
            "chapter_name": await _text(session, chapter["raw"].get("ChapterName"), locale, release_id)
            if chapter else None,
            "act_name": await _text(session, chapter["raw"].get("ActName"), locale, release_id)
            if chapter else None,
            "source": chapter["source"] if chapter else None,
        },
        "quest_tree_nodes": tree_nodes,
        "prerequisites": [
            {"quest": key, "basis": edge.metadata_json.get("source_basis"),
             "name": await _localized(session, name_key_id, locale, release_id),
             "source": {"file": file, "raw_path": path}}
            for edge, key, name_key_id, file, path in prerequisite_rows
        ],
        "required_by": [
            {"quest": key, "basis": edge.metadata_json.get("source_basis"),
             "name": await _localized(session, name_key_id, locale, release_id),
             "source": {"file": file, "raw_path": path}}
            for edge, key, name_key_id, file, path in required_by_rows
        ],
        "quest_source": source["source"] if source else None,
        "ordering_semantics": {
            "quest_tree_next": "Authored next node in the QuestTree UI graph; runtime conditions may gate access.",
            "quest_tree_predecessor": "Explicit predecessor node in QuestTree; it is not a complete playthrough.",
            "quest_array": "Quest membership in a tree node; array order is not promoted to quest chronology.",
        },
    }
