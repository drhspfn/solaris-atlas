"""Snapshot-scoped scene identities and explicit quest ownership."""

from sqlalchemy import func, select

from wuwa_story.db.models.core import Cutscene, DialogueLine, Quest, QuestAction, QuestState
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.storage import FileReference


def scene_query(release_id, q="", playable=True):
    query = select(Cutscene.node_id, Cutscene.cg_name).where(
        select(NodeRevision.id).where(
            NodeRevision.node_id == Cutscene.node_id, NodeRevision.release_id == release_id
        ).exists()
    )
    if q:
        query = query.where(Cutscene.cg_name.icontains(q, autoescape=True))
    if playable:
        query = query.where(select(FileReference.id).where(
            FileReference.owner_node_id == Cutscene.node_id,
            FileReference.reference_type == "cutscene_flow",
            FileReference.metadata_json["publication_complete"].as_boolean().is_(
                True),
        ).exists())
    return query


async def page(session, release_id, q, playable, limit, offset):
    query = scene_query(release_id, q, playable)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(await session.execute(query.order_by(Cutscene.cg_name, Cutscene.node_id)
                                      .limit(limit).offset(offset)))
    return total, rows


async def source_parents(session, child_ids, relations, release_id):
    if not child_ids:
        return []
    return list(await session.execute(
        select(Edge.to_node_id, Edge.from_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .where(Edge.to_node_id.in_(child_ids), RelationType.key.in_(relations), Edge.layer == "source",
               select(EdgeEvidence.id).where(EdgeEvidence.edge_id == Edge.id,
                                             EdgeEvidence.release_id == release_id).exists()).distinct()
    ))


async def owners(session, scene_ids, release_id):
    # Start at the bounded page of scenes, never materialize every quest's graph.
    movies = await source_parents(session, scene_ids, ("plays_cutscene",), release_id)
    actions = {action: state for action, state in await session.execute(
        select(QuestAction.node_id, QuestAction.quest_state_node_id)
        .where(QuestAction.node_id.in_([action for _, action in movies]))
    )} if movies else {}
    refs = await source_parents(session, set(actions.values()),
                                ("references_flow_state", "references_dungeon_flow_state"), release_id)
    owner_ids = {owner for _, owner in refs}
    children = await source_parents(session, owner_ids, ("has_quest_node",), release_id)
    scenes = await source_parents(session, owner_ids, ("presents_scene",), release_id)
    steps = await source_parents(session, {step for _, step in scenes}, ("has_plot_step",), release_id)
    candidates = {owner: {owner} for owner in owner_ids}
    for child, quest in children:
        candidates[child].add(quest)
    for scene_id, step in scenes:
        candidates[scene_id].update(
            quest for source, quest in steps if source == step)
    quest_ids = {quest for group in candidates.values() for quest in group}
    quests = {quest.node_id: (quest, slug) for quest, slug in await session.execute(
        select(Quest, Node.slug).join(Node, Node.id == Quest.node_id)
        .where(Quest.node_id.in_(quest_ids)).order_by(Quest.game_quest_id)
    )} if quest_ids else {}
    matches = set()
    for movie, action in movies:
        for state, owner in refs:
            if actions.get(action) == state:
                matches.update((movie, quest)
                               for quest in candidates[owner] if quest in quests)
    return [(movie, *quests[quest]) for movie, quest in sorted(matches)]


async def scene(session, name, release_id):
    return (await session.execute(scene_query(release_id, playable=False)
                                  .where(Cutscene.cg_name == name))).first()


async def first_line(session, state_keys, release_id):
    return await session.scalar(
        select(Node.canonical_key).join(DialogueLine, DialogueLine.node_id == Node.id)
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .where(QuestState.state_key.in_(state_keys), select(NodeRevision.id).where(
            NodeRevision.node_id == Node.id, NodeRevision.release_id == release_id).exists())
        .order_by(QuestState.state_key, QuestAction.action_index, DialogueLine.source_index, Node.id)
        .limit(1)
    )
