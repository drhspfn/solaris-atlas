"""Explicit source ownership shared by browsing and story research."""

from sqlalchemy import and_, case, literal, or_, select

from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import Edge, EdgeEvidence
from wuwa_story.db.models.ontology import RelationType


async def quests_for_state(session, state_id: int, release_id: int) -> list[int]:
    """Resolve one state's owners without constructing the graph for every quest."""
    from wuwa_story.db.repositories.cutscenes import source_parents

    transcripts = await source_parents(session, [state_id], ("has_transcript_state",), release_id)
    movies = await source_parents(session, {scene for _, scene in transcripts}, ("plays_cutscene",), release_id)
    actions = await source_parents(session, {action for _, action in movies}, ("contains_action",), release_id)
    movie_states = {state for _, state in actions}
    refs = await source_parents(session, {state_id} | movie_states,
                                ("references_flow_state", "references_dungeon_flow_state"), release_id)
    owners = {owner for _, owner in refs} | {owner for _, owner in movies}
    children = await source_parents(session, owners, ("has_quest_node",), release_id)
    scenes = await source_parents(session, owners, ("presents_scene",), release_id)
    steps = await source_parents(session, {step for _, step in scenes}, ("has_plot_step",), release_id)
    candidates = owners | {quest for _, quest in children} | {quest for _, quest in steps}
    return list(await session.scalars(select(Quest.node_id).where(Quest.node_id.in_(candidates)))) if candidates else []


def quest_states_query(quest_node_id: int, release_id: int | None):
    """Traverse the finite set of source paths owned by one quest in one snapshot."""
    # Phases: quest, plot step, quest-node/scene, state, action, movie, transcript.
    walk = select(Quest.node_id.label("node_id"), literal(0).label("phase")).where(
        Quest.node_id == quest_node_id
    ).cte("quest_walk", recursive=True)
    references = ("references_flow_state", "references_dungeon_flow_state")
    transitions = or_(
        and_(walk.c.phase == 0, RelationType.key.in_(("has_quest_node", "has_plot_step", *references))),
        and_(walk.c.phase == 1, RelationType.key == "presents_scene"),
        and_(walk.c.phase == 2, RelationType.key.in_(references)),
        and_(walk.c.phase.in_((0, 2)), RelationType.key == 'plays_cutscene'),
        and_(walk.c.phase == 3, RelationType.key == "contains_action"),
        and_(walk.c.phase == 4, RelationType.key == "plays_cutscene"),
        and_(walk.c.phase == 5, RelationType.key == "has_transcript_state"),
    )
    next_phase = case(
        (RelationType.key == "has_plot_step", 1),
        (RelationType.key.in_(("has_quest_node", "presents_scene")), 2),
        (RelationType.key.in_(references), 3),
        (RelationType.key == "contains_action", 4),
        (RelationType.key == "plays_cutscene", 5),
        else_=6,
    )
    edges = (
        select(Edge.to_node_id, next_phase)
        .select_from(walk)
        .join(Edge, Edge.from_node_id == walk.c.node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.layer == "source", transitions)
    )
    if release_id is not None:
        edges = edges.where(select(EdgeEvidence.id).where(
            EdgeEvidence.edge_id == Edge.id, EdgeEvidence.release_id == release_id
        ).exists())
    walk = walk.union(edges)
    return select(walk.c.node_id).where(walk.c.phase.in_((3, 6))).distinct()


def quest_state_links(release_id: int | None = None, *, quest_node_id: int | None = None):
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
    direct_owner = select(Quest.node_id.label("quest_id"), Quest.node_id.label("owner_id"))
    child_owner = select(Quest.node_id, children.c.child).join(
        children, children.c.parent == Quest.node_id
    )
    scene_owner = (select(Quest.node_id, scenes.c.child)
                   .join(steps, steps.c.parent == Quest.node_id)
                   .join(scenes, scenes.c.parent == steps.c.child))
    if quest_node_id is not None:
        direct_owner = direct_owner.where(Quest.node_id == quest_node_id)
        child_owner = child_owner.where(Quest.node_id == quest_node_id)
        scene_owner = scene_owner.where(Quest.node_id == quest_node_id)
    owners = direct_owner.union(child_owner, scene_owner).subquery()
    references = (
        select(source_edges("references_flow_state"))
        .union(select(source_edges("references_dungeon_flow_state")))
        .subquery()
    )
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
            select(owners.c.quest_id, transcripts.c.child)
            .join(movies, movies.c.parent == owners.c.owner_id)
            .join(transcripts, transcripts.c.parent == movies.c.child),
            select(direct.c.quest_id, transcripts.c.child)
            .join(actions, actions.c.parent == direct.c.state_id)
            .join(movies, movies.c.parent == actions.c.child)
            .join(transcripts, transcripts.c.parent == movies.c.child)
        )
        .subquery()
    )
