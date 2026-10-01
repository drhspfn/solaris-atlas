"""Bounded paths through source-backed graph edges, without semantic inference."""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import Edge, EdgeEvidence
from wuwa_story.db.models.ontology import RelationType

QUEST_SEQUENCE_RELATIONS = frozenset({
    "quest_tree_contains_quest", "quest_tree_predecessor", "quest_tree_next",
    "requires_quest",
})
NARRATIVE_RELATIONS = QUEST_SEQUENCE_RELATIONS | frozenset({
    "quest_tree_main_node", "quest_tree_includes_node",
    "in_quest_tree_chapter", "in_quest_chapter",
    "has_quest_node", "parent_of", "checks_quest_node", "condition_slot",
    "branch_target", "has_condition_branch", "has_plot_step",
    "next_authored_plot_step", "presents_scene", "references_flow_state",
    "has_flow_state", "contains_action", "next_source_action",
    "presents_talk_item", "next_authored_talk", "presents_choice",
    "choice_branch", "sequence_transition", "jumps_to_talk", "spoken_by",
    "references_quest", "references_character", "references_area",
    "references_item", "plays_cutscene",
})


async def find_source_path(
    session: AsyncSession,
    start_id: int,
    target_id: int,
    *,
    release_id: int | None,
    relation_keys: frozenset[str],
    max_depth: int = 6,
    max_nodes: int = 1500,
    max_edges: int = 10000,
) -> tuple[list[tuple[Edge, str, int, int]] | None, bool]:
    """Return shortest undirected source path and whether search hit a safety bound.

    Each hop retains the authored edge direction. A path is a sequence of source
    references; it does not prove event chronology or causal relation.
    """
    if not 1 <= max_depth <= 8 or not 2 <= max_nodes <= 5000:
        raise ValueError("path bounds are outside the supported range")
    if start_id == target_id:
        return [], False
    parents: dict[int, tuple[int, Edge, str] | None] = {start_id: None}
    frontier = [start_id]
    edges_seen = 0
    for _depth in range(max_depth):
        if not frontier:
            break
        frontier_ids = set(frontier)
        statement = (
            select(Edge, RelationType.key)
            .join(RelationType, RelationType.id == Edge.relation_type_id)
            .where(
                Edge.layer.in_(("source", "canonical")),
                RelationType.key.in_(relation_keys),
                or_(Edge.from_node_id.in_(frontier), Edge.to_node_id.in_(frontier)),
            )
            .order_by(RelationType.key, Edge.id)
        )
        if release_id is not None:
            statement = statement.where(
                Edge.id.in_(
                    select(EdgeEvidence.edge_id).where(EdgeEvidence.release_id == release_id)
                )
            )
        remaining = max_edges - edges_seen
        rows = list((await session.execute(statement.limit(remaining + 1))).all())
        if len(rows) > remaining:
            return None, True
        edges_seen += len(rows)
        next_frontier = []
        for edge, relation in rows:
            if edge.from_node_id in frontier_ids:
                previous, neighbor = edge.from_node_id, edge.to_node_id
            else:
                previous, neighbor = edge.to_node_id, edge.from_node_id
            if neighbor in parents:
                continue
            if len(parents) >= max_nodes:
                return None, True
            parents[neighbor] = (previous, edge, relation)
            if neighbor == target_id:
                path: list[tuple[Edge, str, int, int]] = []
                cursor = target_id
                while cursor != start_id:
                    parent = parents[cursor]
                    assert parent is not None
                    previous, found_edge, found_relation = parent
                    path.append((found_edge, found_relation, previous, cursor))
                    cursor = previous
                path.reverse()
                return path, False
            next_frontier.append(neighbor)
        frontier = next_frontier
    return None, False


async def source_path_evidence(
    session: AsyncSession, edge_ids: set[int], release_id: int | None
) -> dict[int, dict[str, Any]]:
    if not edge_ids:
        return {}
    statement = select(EdgeEvidence).where(EdgeEvidence.edge_id.in_(edge_ids))
    if release_id is not None:
        statement = statement.where(EdgeEvidence.release_id == release_id)
    rows = list(await session.scalars(statement.order_by(EdgeEvidence.edge_id, EdgeEvidence.id)))
    result = {}
    for evidence in rows:
        result.setdefault(evidence.edge_id, {
            "release_id": evidence.release_id,
            "source_file": evidence.source_file_path,
            "raw_path": evidence.source_raw_path,
            "source_record_id": evidence.source_record_id,
            "explanation": evidence.explanation,
        })
    return result
