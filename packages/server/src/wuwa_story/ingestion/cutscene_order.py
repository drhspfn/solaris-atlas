"""Build a snapshot-local cutscene order from the authored quest map."""
from collections import defaultdict

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.core import (
    CutsceneOrderRevision,
    CutsceneStoryOrder,
    QuestAction,
    QuestNode,
)
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories import cutscenes
from wuwa_story.story.domain import authored_order

QUESTS = "BinData/QuestData/questdata.json"
TREE = "BinData/QuestTree/questtreenode.json"


def quest_order(quests, tree):
    """Use the same chapter and authored navigation order as the story map."""
    groups = defaultdict(list)
    if tree:
        for row in tree:
            if isinstance(row.get("Id"), int) and isinstance(row.get("ChapterId"), int):
                groups[row["ChapterId"]].append(row)
    else:
        for row in quests:
            data = row.get("Data") or {}
            quest_id = row.get("QuestId")
            chapter = data.get("ChapterId") if isinstance(data, dict) else None
            if not isinstance(quest_id, int) or not isinstance(chapter, int) or chapter <= 0:
                continue
            provide = data.get("ProvideType") or {}
            conditions = provide.get("Conditions", []) if isinstance(
                provide, dict) else []
            previous = [item.get("PreQuest") for item in conditions if isinstance(item, dict)
                        and item.get("Type") == "PreQuest" and isinstance(item.get("PreQuest"), int)]
            groups[chapter].append(
                {"Id": quest_id, "PreNode": previous, "QuestArray": [quest_id]})
    result = {}
    for chapter in sorted(groups):
        for node in authored_order(groups[chapter]):
            for quest in node.get("QuestArray", []):
                if isinstance(quest, int) and quest not in result:
                    result[quest] = len(result)
    return result


async def project(session, release_id):
    """Derive the ordered rows without changing the snapshot."""
    records = list(await session.execute(
        select(SourceFile.logical_source_path, SourceRecord.data)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(SourceRecord.release_id == release_id, SourceFile.logical_source_path.in_((QUESTS, TREE)))
        .order_by(SourceFile.logical_source_path, SourceRecord.row_index)
    ))
    quests = [data for path, data in records if path == QUESTS]
    tree = [data for path, data in records if path == TREE]
    ranks = quest_order(quests, tree)
    scenes = list(await session.execute(cutscenes.scene_query(release_id, playable=False)))
    owners = await cutscenes.owners(session, [scene.node_id for scene in scenes], release_id)
    movies = await cutscenes.source_parents(session, [scene.node_id for scene in scenes],
                                           ("plays_cutscene",), release_id)
    actions = {action: (state, index) for action, state, index in await session.execute(
        select(QuestAction.node_id, QuestAction.quest_state_node_id, QuestAction.action_index)
        .where(QuestAction.node_id.in_([action for _, action in movies]))
    )} if movies else {}
    refs = await cutscenes.source_parents(session, {state for state, _ in actions.values()},
                                          ("references_flow_state",), release_id)
    nodes = {node: (quest, row) for node, quest, row in await session.execute(
        select(QuestNode.node_id, QuestNode.game_quest_id, SourceRecord.row_index)
        .join(NodeRevision, NodeRevision.node_id == QuestNode.node_id)
        .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
        .where(NodeRevision.release_id == release_id, QuestNode.node_id.in_([owner for _, owner in refs]))
    )} if refs else {}
    positions = defaultdict(list)
    for scene_id, action in movies:
        if action not in actions:
            continue
        state, action_index = actions[action]
        for target, owner in refs:
            if target == state and owner in nodes and nodes[owner][1] is not None:
                quest_id, row = nodes[owner]
                positions[(scene_id, quest_id)].append((row, action_index))
    ranked = defaultdict(list)
    for scene_id, quest, _, inferred in owners:
        if quest.game_quest_id in ranks:
            position = max(positions.get((scene_id, quest.game_quest_id), [(-1, -1)]))
            ranked[scene_id].append((ranks[quest.game_quest_id], inferred, position))
    values = []
    for scene in scenes:
        choices = ranked.get(scene.node_id, [])
        # Prefer an explicit link; choose the latest ranked quest when a scene is reused.
        explicit = [(rank, position) for rank, inferred, position in choices if not inferred]
        inferred = [(rank, position) for rank, is_inferred, position in choices if is_inferred]
        rank, position = max(explicit or inferred, default=(None, (-1, -1)))
        values.append({"release_id": release_id, "cutscene_node_id": scene.node_id,
                       "quest_rank": rank, "basis": "quest" if explicit else "inferred" if inferred else "fallback",
                       "_name": scene.cg_name, "_position": position})
    values.sort(key=lambda value: (value["quest_rank"] is None, value["quest_rank"] or 0,
                                   value["_position"], value["_name"], value["cutscene_node_id"]))
    for index, value in enumerate(values):
        value["sort_index"] = index
        del value["_name"]
        del value["_position"]
    return values


async def rebuild(session, release_id):
    """Replace one snapshot atomically; concurrent rebuilds of that snapshot serialize."""
    await session.execute(text("SELECT pg_advisory_xact_lock(704, :id)"), {"id": release_id})
    values = await project(session, release_id)
    await session.execute(delete(CutsceneStoryOrder).where(CutsceneStoryOrder.release_id == release_id))
    if values:
        await session.execute(insert(CutsceneStoryOrder).values(values))
    await session.execute(update(CutsceneOrderRevision).where(CutsceneOrderRevision.id == 1)
                          .values(revision=CutsceneOrderRevision.revision + 1))
    return len(values), sum(value["quest_rank"] is not None for value in values)
