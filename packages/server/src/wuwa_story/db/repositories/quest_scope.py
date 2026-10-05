"""Explicit source ownership shared by browsing and story research."""

from sqlalchemy import select

from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import Edge, EdgeEvidence
from wuwa_story.db.models.ontology import RelationType


def quest_state_links(release_id: int | None = None):
    def source_edges(relation):
        query = (
            select(Edge.from_node_id.label("parent"), Edge.to_node_id.label("child"))
            .join(RelationType, RelationType.id == Edge.relation_type_id)
            .where(Edge.layer == "source", RelationType.key == relation)
        )
        if release_id is not None:
            query = query.where(
                select(EdgeEvidence.id)
                .where(EdgeEvidence.edge_id == Edge.id, EdgeEvidence.release_id == release_id)
                .exists()
            )
        return query.subquery()

    children = source_edges("has_quest_node")
    steps = source_edges("has_plot_step")
    scenes = source_edges("presents_scene")
    owners = (
        select(Quest.node_id.label("quest_id"), Quest.node_id.label("owner_id"))
        .union(
            select(Quest.node_id, children.c.child).join(
                children, children.c.parent == Quest.node_id
            ),
            select(Quest.node_id, scenes.c.child)
            .join(steps, steps.c.parent == Quest.node_id)
            .join(scenes, scenes.c.parent == steps.c.child),
        )
        .subquery()
    )
    references = source_edges("references_flow_state")
    direct = (
        select(owners.c.quest_id, references.c.child.label("state_id"))
        .join(references, references.c.parent == owners.c.owner_id)
        .distinct()
        .subquery()
    )
    actions = source_edges("contains_action")
    movies = source_edges("plays_cutscene")
    transcripts = source_edges("has_transcript_state")
    return (
        select(direct.c.quest_id, direct.c.state_id)
        .union(
            select(direct.c.quest_id, transcripts.c.child)
            .join(actions, actions.c.parent == direct.c.state_id)
            .join(movies, movies.c.parent == actions.c.child)
            .join(transcripts, transcripts.c.parent == movies.c.child)
        )
        .subquery()
    )
