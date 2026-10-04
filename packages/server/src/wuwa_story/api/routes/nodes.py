from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.source_labels import source_family_label
from wuwa_story.db.models.core import DialogueLine, Quest, QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.repositories.edges import get_edges
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.session import get_session

router = APIRouter(prefix="/nodes", tags=["nodes"])

class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canonical_key: str
    slug: str | None
    type_id: int
    status: str
    metadata_json: dict[str, Any]


class EdgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_node_id: int
    relation_type_id: int
    to_node_id: int
    layer: str
    basis: str
    confidence: float | None
    metadata_json: dict[str, Any]


@router.get("/{canonical_key:path}/edges", response_model=list[EdgeResponse])
async def node_edges(
    canonical_key: str,
    direction: str = Query("both", pattern="^(in|out|both)$"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[EdgeResponse]:
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    edges = await get_edges(session, node.id, direction=direction, limit=limit)
    return [EdgeResponse.model_validate(edge) for edge in edges]


@router.get("/{canonical_key:path}/related")
async def node_related(
    canonical_key: str,
    direction: str = Query("both", pattern="^(in|out|both)$"),
    relation: str | None = None,
    category: str | None = None,
    source_file: str | None = None,
    include_evidence: bool = False,
    locale: str = "en",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Browse explicit graph neighbors with optional relation and node-type filters."""
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    neighbor_id = case((Edge.from_node_id == node.id, Edge.to_node_id), else_=Edge.from_node_id)
    direction_clause = (
        Edge.from_node_id == node.id
        if direction == "out"
        else Edge.to_node_id == node.id
        if direction == "in"
        else (Edge.from_node_id == node.id) | (Edge.to_node_id == node.id)
    )
    statement = (
        select(Edge, RelationType.key, Node, NodeType.key)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(Node, Node.id == neighbor_id)
        .join(NodeType, NodeType.id == Node.type_id)
        .where(
            Edge.layer.in_(["source", "canonical"]),
            direction_clause,
        )
    )
    if relation:
        statement = statement.where(RelationType.key == relation)
    if category:
        statement = statement.where(NodeType.key == category)
    evidence_requested = include_evidence or category == "source_reference" or source_file is not None
    if not evidence_requested:
        statement = statement.where(NodeType.key != "source_reference")
    if source_file:
        statement = statement.where(Node.metadata_json["source_file"].as_string() == source_file)
    locale_id = await session.scalar(select(Locale.id).where(Locale.code == locale))
    label = (
        select(SearchDocument.title)
        .where(
            SearchDocument.target_node_id == Node.id,
            SearchDocument.locale_id == locale_id,
            SearchDocument.category.in_(
                ["character", "character_nickname", "item", "area", "quest", "speaker"]
            ),
        )
        .order_by(
            case(
                (SearchDocument.category == NodeType.key, 0),
                else_=1,
            ),
            SearchDocument.id,
        )
        .limit(1)
        .scalar_subquery()
    )
    statement = statement.add_columns(label.label("localized_label"))
    rows = await session.execute(
        statement.order_by(RelationType.key, Node.canonical_key, Edge.id)
        .offset(offset)
        .limit(limit)
    )
    source_file_value = Node.metadata_json["source_file"].as_string()
    evidence_query = (
        select(source_file_value, RelationType.key, func.count())
        .select_from(Edge)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(
            Node,
            Node.id
            == case((Edge.from_node_id == node.id, Edge.to_node_id), else_=Edge.from_node_id),
        )
        .join(NodeType, NodeType.id == Node.type_id)
        .where(
            direction_clause,
            Edge.layer.in_(["source", "canonical"]),
            NodeType.key == "source_reference",
        )
        .group_by(source_file_value, RelationType.key)
        .order_by(func.count().desc(), source_file_value, RelationType.key)
    )
    if relation:
        evidence_query = evidence_query.where(RelationType.key == relation)
    evidence_rows = await session.execute(evidence_query)
    evidence_summary = [
        {
            "label": source_family_label(file),
            "source_file": file,
            "relation": relation_key,
            "record_count": count,
        }
        for file, relation_key, count in evidence_rows
    ]
    results = []
    for edge, relation_key, neighbor, node_type, localized_label in rows:
        fallback = neighbor.slug
        if fallback is None and node_type == "source_reference":
            source_file = neighbor.metadata_json.get("source_file")
            source_row = neighbor.metadata_json.get("source_row")
            if source_file:
                fallback = f"{source_file}" + (f" row {source_row}" if source_row is not None else "")
        results.append(
            {
                "relation": relation_key,
                "direction": "outgoing" if edge.from_node_id == node.id else "incoming",
                "node": {
                    "id": neighbor.id,
                    "canonical_key": neighbor.canonical_key,
                    "type": node_type,
                    "label": localized_label or fallback or neighbor.canonical_key,
                },
                "basis": edge.basis,
                "provenance": edge.metadata_json,
            }
        )
    return {
        "node": canonical_key,
        "summary": (
            f"Found {len(results)} linked entities."
            if results
            else "No linked entity neighbors were found. Source records below are raw game configuration references, not inferred character or story relationships."
        ),
        "direction": direction,
        "relation_filter": relation,
        "category_filter": category,
        "source_file_filter": source_file,
        "include_evidence": evidence_requested,
        "source_evidence": evidence_summary,
        "locale": locale,
        "limit": limit,
        "offset": offset,
        "results": results,
    }


@router.get("/{canonical_key:path}/source-record")
async def node_source_record(
    canonical_key: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the raw upstream record that backs a canonical node, when available."""
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    node_type = await session.get(NodeType, node.type_id)
    row = await session.execute(
        select(NodeRevision, SourceRecord, SourceFile, GameRelease)
        .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .outerjoin(GameRelease, GameRelease.id == NodeRevision.release_id)
        .where(NodeRevision.node_id == node.id)
        .order_by(NodeRevision.revision.desc(), NodeRevision.id.desc())
        .limit(1)
    )
    result = row.first()
    if result is None:
        return {
            "canonical_key": canonical_key,
            "type": node_type.key if node_type else None,
            "source_record": None,
            "resolution": "no_raw_record_linked",
        }
    revision, record, source_file, release = result
    return {
        "canonical_key": canonical_key,
        "type": node_type.key if node_type else None,
        "source_record": {
            "id": record.id,
            "source_file": source_file.logical_source_path,
            "row": record.row_index,
            "source_key": record.source_key,
            "version": release.game_version if release else None,
            "raw_path": revision.metadata_json.get("source_raw_path"),
            "raw_record": record.data,
        },
        "resolution": "linked_by_source_record_id",
    }


@router.get("/{canonical_key:path}/narrative-context")
async def node_narrative_context(
    canonical_key: str,
    locale: str = "en",
    game_version: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Resolve authored context for a dialogue node using its canonical source joins."""
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    node_type = await session.get(NodeType, node.type_id)
    if node_type is None or node_type.key not in {"talk_item", "dialogue_line", "narration", "player_choice"}:
        return {"canonical_key": canonical_key, "available": False, "reason": "node is not a dialogue-like entry"}

    line = await session.get(DialogueLine, node.id)
    if line is None or line.action_node_id is None:
        return {"canonical_key": canonical_key, "available": False, "reason": "no normalized DialogueLine is linked to this node"}

    from wuwa_story.api.routes.story import _dialogue_payload, _quest_info, _release_id
    from wuwa_story.api.routes.story.shared import _quest_state_links

    release_id = await _release_id(session, game_version)
    line_query = (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .join(Node, Node.id == DialogueLine.node_id)
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
    )
    target_row = (await session.execute(line_query.where(DialogueLine.node_id == node.id))).first()
    if target_row is None:
        return {"canonical_key": canonical_key, "available": False, "reason": "dialogue source row is incomplete"}
    target_payload = await _dialogue_payload(
        session, target_row, locale_code=locale, release_id=release_id
    )
    def compact_dialogue(payload: dict[str, Any]) -> dict[str, Any]:
        """Keep display/provenance fields without repeating an entire ShowTalk blob."""
        action = payload.get("action")
        if action:
            action.pop("params", None)
        provenance = payload.get("provenance")
        if provenance:
            provenance.pop("raw_record", None)
        for choice in payload.get("player_choices", []):
            choice_provenance = choice.get("provenance")
            if choice_provenance:
                choice_provenance.pop("raw_record", None)
        return payload

    target_payload = compact_dialogue(target_payload)
    target_action = target_row[1]
    target_state = target_row[2]

    context_rows = list(
        (
            await session.execute(
                line_query.where(
                    DialogueLine.action_node_id == target_action.node_id,
                    DialogueLine.source_index.between(
                        max(0, (line.source_index or 0) - 3),
                        (line.source_index or 0) + 3,
                    ),
                ).order_by(DialogueLine.source_index, DialogueLine.node_id)
            )
        ).all()
    )
    context = [
        compact_dialogue(
            await _dialogue_payload(session, row, locale_code=locale, release_id=release_id)
        )
        for row in context_rows
    ]

    state_owners = (
        select(Edge.from_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(
            Edge.to_node_id == target_state.node_id,
            RelationType.key == "references_flow_state",
            Edge.layer == "source",
        )
    )
    ownership = _quest_state_links()
    quest_rows = await session.scalars(
        select(Quest)
        .where(
            Quest.node_id.in_(
                select(ownership.c.quest_id).where(ownership.c.state_id == target_state.node_id)
            )
        )
        .order_by(Quest.game_quest_id)
        .limit(20)
    )
    quests = [await _quest_info(session, quest, locale, game_version) for quest in quest_rows]

    source_rows = await session.execute(
        select(SourceRecord, SourceFile)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(SourceRecord.id == target_state.source_record_id)
    )
    state_source = source_rows.first()
    owner_keys = list(
        await session.scalars(select(Node.canonical_key).where(Node.id.in_(state_owners)))
    )
    return {
        "canonical_key": canonical_key,
        "available": True,
        "dialogue": target_payload,
        "context": context,
        "flow_state": {
            "state_key": target_state.state_key,
            "canonical_key": f"flow_state:{target_state.state_key}",
            "source": {
                "file": state_source[1].logical_source_path if state_source else None,
                "row": state_source[0].row_index if state_source else None,
                "record_id": target_state.source_record_id,
            },
        },
        "quests": quests,
        "game_version": (await session.get(GameRelease, release_id)).game_version if release_id else None,
        "quest_resolution": {
            "basis": "source references_flow_state from quest, has_quest_node child, or has_plot_step/presents_scene scene",
            "quest_node_refs": owner_keys,
            "unresolved": not bool(quests),
        },
        "ordering": "source_index within one authored ShowTalk action; this context window is not runtime branch traversal",
    }


@router.get("/{canonical_key:path}", response_model=NodeResponse)
async def node_detail(
    canonical_key: str, session: AsyncSession = Depends(get_session)
) -> NodeResponse:
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return NodeResponse.model_validate(node)
