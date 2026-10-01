"""Source-backed navigation between two canonical graph nodes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.shared import _release_id
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.session import get_session
from wuwa_story.graph.pathfinding import (
    NARRATIVE_RELATIONS,
    QUEST_SEQUENCE_RELATIONS,
    find_source_path,
    source_path_evidence,
)

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/path")
async def graph_path(
    from_key: str = Query(alias="from"),
    to_key: str = Query(alias="to"),
    mode: str = Query("quest_sequence", pattern="^(quest_sequence|narrative)$"),
    game_version: str | None = None,
    max_depth: int = Query(6, ge=1, le=8),
    max_nodes: int = Query(1500, ge=2, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Find a bounded shortest path, preserving each source edge's direction and evidence."""
    nodes = list(await session.scalars(select(Node).where(Node.canonical_key.in_((from_key, to_key)))))
    by_key = {node.canonical_key: node for node in nodes}
    if from_key not in by_key or to_key not in by_key:
        raise HTTPException(status_code=404, detail="source or target node not found")
    release_id = await _release_id(session, game_version)
    if game_version and release_id is None:
        raise HTTPException(status_code=404, detail="game version not imported")
    release = await session.get(GameRelease, release_id) if release_id else None
    relations = QUEST_SEQUENCE_RELATIONS if mode == "quest_sequence" else NARRATIVE_RELATIONS
    path, truncated = await find_source_path(
        session, by_key[from_key].id, by_key[to_key].id,
        release_id=release_id, relation_keys=relations,
        max_depth=max_depth, max_nodes=max_nodes,
    )
    if path is None:
        return {
            "from": from_key, "to": to_key, "mode": mode,
            "game_version": release.game_version if release else None,
            "found": False, "truncated": truncated, "hops": [],
            "meaning": "No path was found within the selected source relations and limits."
            if not truncated else "Search bound reached; absence of a path is not established.",
        }
    node_ids = {node_id for _edge, _relation, previous, next_id in path for node_id in (previous, next_id)}
    path_nodes = {
        node.id: node.canonical_key
        for node in await session.scalars(select(Node).where(Node.id.in_(node_ids)))
    }
    evidence = await source_path_evidence(session, {edge.id for edge, *_ in path}, release_id)
    return {
        "from": from_key, "to": to_key, "mode": mode,
        "game_version": release.game_version if release else None,
        "found": True, "truncated": False,
        "hops": [
            {
                "from": path_nodes[previous], "to": path_nodes[next_id],
                "relation": relation,
                "edge_direction": "forward" if edge.from_node_id == previous else "reverse",
                "basis": edge.basis, "edge_id": edge.id,
                "source": evidence.get(edge.id),
            }
            for edge, relation, previous, next_id in path
        ],
        "meaning": "This is a source-reference path, not proof of chronology, causality, or a played route.",
    }
