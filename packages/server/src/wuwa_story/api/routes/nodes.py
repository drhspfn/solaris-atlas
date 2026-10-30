from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.api.source_labels import source_family_label
from wuwa_story.db.models.core import DialogueLine, Quest, QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, Node, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.repositories.edges import get_edges
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.repositories.story_browsing import latest_source_row
from wuwa_story.db.session import get_session
from wuwa_story.read_models.context import SOURCE_BUILD
from wuwa_story.urls import node_address

router = APIRouter(prefix="/nodes", tags=["nodes"])


def _incident_edges(node_id: int, direction: str):
    """Resolve indexed endpoints before joining the potentially large node table."""
    def branch(endpoint, neighbor):
        return select(
            Edge.id.label("edge_id"), Edge.relation_type_id,
            neighbor.label("neighbor_id"),
        ).where(endpoint == node_id, Edge.layer.in_(["source", "canonical"]))

    outgoing = branch(Edge.from_node_id, Edge.to_node_id)
    incoming = branch(Edge.to_node_id, Edge.from_node_id)
    if direction == "out":
        statement = outgoing
    elif direction == "in":
        statement = incoming
    else:
        # A self-loop belongs to both directions but occurs once in "both".
        statement = outgoing.union_all(incoming.where(Edge.from_node_id != node_id))
    return statement.cte("incident_edges").prefix_with("MATERIALIZED", dialect="postgresql")


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
    if read_projections.should_route_node_to_projection(canonical_key):
        return await read_projections.node_section(session, canonical_key, "edges", direction=direction, limit=limit)
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
    if read_projections.should_route_node_to_projection(canonical_key, locale=locale):
        return await read_projections.node_related_page(
            session, canonical_key, direction, relation, category, source_file, include_evidence, locale, limit, offset,
        )
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    incident = _incident_edges(node.id, direction)
    statement = (
        select(Edge, RelationType.key, Node, NodeType.key)
        .select_from(incident)
        .join(Edge, Edge.id == incident.c.edge_id)
        .join(RelationType, RelationType.id == incident.c.relation_type_id)
        .join(Node, Node.id == incident.c.neighbor_id)
        .join(NodeType, NodeType.id == Node.type_id)
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
    # Evidence is independent of page, category, source-file filter and locale.
    # Only source builders own a bounded request-cache lifetime on this session.
    evidence_cache = session.info.setdefault("node_related_evidence", {}) if SOURCE_BUILD.get() else None
    evidence_key = (node.id, direction, relation)
    evidence_summary = evidence_cache.get(evidence_key) if evidence_cache is not None else None
    if evidence_summary is None:
        source_file_value = Node.metadata_json["source_file"].as_string()
        evidence_query = (
            select(source_file_value, RelationType.key, func.count())
            .select_from(incident)
            .join(RelationType, RelationType.id == incident.c.relation_type_id)
            .join(Node, Node.id == incident.c.neighbor_id)
            .join(NodeType, NodeType.id == Node.type_id)
            .where(NodeType.key == "source_reference")
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
        if evidence_cache is not None:
            evidence_cache[evidence_key] = evidence_summary
    results = []
    for edge, relation_key, neighbor, node_type, localized_label in rows:
        fallback = None
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
                    **node_address(neighbor, node_type),
                    "type": node_type,
                    "label": localized_label or fallback or neighbor.canonical_key,
                },
                "basis": edge.basis,
                "provenance": edge.metadata_json,
            }
        )
    return {
        "node": node.canonical_key,
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
    if read_projections.should_route_node_to_projection(canonical_key):
        return await read_projections.node_section(session, canonical_key, "source_record")
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    node_type = await session.get(NodeType, node.type_id)
    result = await latest_source_row(session, node.id)
    if result is None:
        return {
            "canonical_key": node.canonical_key,
            "type": node_type.key if node_type else None,
            "source_record": None,
            "resolution": "no_raw_record_linked",
        }
    revision, record, source_file, release = result
    return {
        "canonical_key": node.canonical_key,
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
    if read_projections.should_route_node_to_projection(canonical_key, game_version, locale=locale):
        return await read_projections.node_section(session, canonical_key, "narrative", locale, game_version)
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    node_type = await session.get(NodeType, node.type_id)
    if node_type is None or node_type.key not in {"talk_item", "dialogue_line", "narration", "player_choice"}:
        return {"canonical_key": node.canonical_key, "available": False, "reason": "node is not a dialogue-like entry"}

    line = await session.get(DialogueLine, node.id)
    if line is None or line.action_node_id is None:
        return {"canonical_key": node.canonical_key, "available": False, "reason": "no normalized DialogueLine is linked to this node"}

    from wuwa_story.api.routes.story import _dialogue_payload, _quest_info, _release_id
    from wuwa_story.api.routes.story.shared import _quest_state_links
    from wuwa_story.db.repositories.quest_scope import quests_for_state
    from wuwa_story.story.service import prefetch_dialogue

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
        return {"canonical_key": node.canonical_key, "available": False, "reason": "dialogue source row is incomplete"}
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
    await prefetch_dialogue(session, [target_row, *context_rows], locale, release_id)
    target_payload = compact_dialogue(
        await _dialogue_payload(session, target_row, locale_code=locale, release_id=release_id)
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
    ownership = _quest_state_links(release_id) if release_id is None else None
    quest_ids = (await quests_for_state(session, target_state.node_id, release_id)
                 if release_id is not None else select(ownership.c.quest_id).where(
                     ownership.c.state_id == target_state.node_id))
    quest_rows = await session.scalars(
        select(Quest)
        .where(
            Quest.node_id.in_(quest_ids)
        )
        .order_by(Quest.game_quest_id)
        .limit(20)
    )
    quests = [await _quest_info(session, quest, locale, game_version) for quest in quest_rows]

    # A caption-matched transcript may belong to a scene reached through LevelPlay,
    # even when that dialogue state is not part of the quest's ordered transcript.
    if release_id is not None:
        from wuwa_story.db.repositories.cutscenes import source_parents

        scene_refs = await source_parents(session, [target_state.node_id], ("has_transcript_state",), release_id)
        scene_ids = {scene for _, scene in scene_refs}
        movies = await source_parents(session, scene_ids, ("plays_cutscene",), release_id)
        actions = {action.node_id: action for action in await session.scalars(
            select(QuestAction).where(QuestAction.node_id.in_({action for _, action in movies}))
        )} if movies else {}
        memberships = set(await source_parents(session, actions.keys(), ("contains_action",), release_id))
        states = {state.node_id: state for state in await session.scalars(
            select(QuestState).where(QuestState.node_id.in_({action.quest_state_node_id for action in actions.values()}))
        )} if actions else {}
        refs = await source_parents(session, states.keys(), ("references_scenario_flow_state",), release_id)
        candidates = {quest.node_id: quest for quest in await session.scalars(
            select(Quest).where(Quest.node_id.in_({owner for _, owner in refs}))
        )} if refs else {}
        scene_keys = dict((await session.execute(
            select(Node.id, Node.canonical_key).where(Node.id.in_(scene_ids))
        )).all()) if scene_ids else {}
        seen = {quest["game_quest_id"] for quest in quests}
        for scene_id, action_id in movies:
            action = actions.get(action_id)
            state = states.get(action.quest_state_node_id) if action else None
            if not state or (action_id, state.node_id) not in memberships:
                continue
            for state_id, owner in refs:
                quest = candidates.get(owner)
                if state_id != state.node_id or not quest or quest.game_quest_id in seen:
                    continue
                quests.append({**(await _quest_info(session, quest, locale, game_version)),
                               "context_inferred": True,
                               "scene": {"reference": scene_keys[scene_id], "flow_state": state.state_key,
                                         "action_index": action.action_index}})
                seen.add(quest.game_quest_id)

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
        "canonical_key": node.canonical_key,
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
            "basis": "Quest and scenario references; PlayMovie transcripts match complete caption keys. Scenario context does not prove every runtime branch occurred.",
            "quest_node_refs": owner_keys,
            "unresolved": not bool(quests),
        },
        "ordering": "source_index within one authored ShowTalk action; this context window is not runtime branch traversal",
    }


@router.get("/{canonical_key:path}", response_model=NodeResponse)
async def node_detail(
    canonical_key: str, session: AsyncSession = Depends(get_session)
) -> NodeResponse:
    if read_projections.should_route_node_to_projection(canonical_key):
        return NodeResponse.model_validate(await read_projections.node_section(session, canonical_key, "node"))
    node = await get_node(session, canonical_key)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return NodeResponse.model_validate(node)
