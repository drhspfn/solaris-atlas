from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.db.models.core import DialogueLine, Quest, QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, Node, NodeType
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.edges import get_edges
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.repositories.story_browsing import latest_source_row
from wuwa_story.db.session import get_session
from wuwa_story.story.node_related import _incident_edges as _incident_edges
from wuwa_story.story.node_related import source_related

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
    return await source_related(
        session, node.id, node.canonical_key, direction=direction, relation=relation,
        category=category, source_file=source_file, include_evidence=include_evidence,
        locale=locale, limit=limit, offset=offset,
    )

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
