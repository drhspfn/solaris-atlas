"""Project source-authored tree positions onto quest and LevelPlay flow states."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select

from wuwa_story.db.models.core import AuthoredTreeOrder, QuestAction, QuestState
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.cutscenes import source_parents
from wuwa_story.ingestion.dungeon_context import walk
from wuwa_story.story.media import media_links


def records_match(nodes: list[dict], records: dict[int, dict]) -> bool:
    if {node['id'] for node in nodes} != set(records):
        return False
    for node in nodes:
        raw = json.dumps(records[node['id']], sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False).encode('utf-8')
        if hashlib.sha256(raw).hexdigest() != node.get('record_sha256'):
            return False
    return True


def flow_positions(quest_nodes: list[dict], quest_records: dict[int, dict],
                   level_trees: dict[int, tuple[list[dict], dict[int, dict]]],
                   valid_states: set[str]) -> list[tuple[str, tuple[int, ...]]]:
    """Return authored flow positions; branches remain source order, not runtime history."""
    if not records_match(quest_nodes, quest_records):
        return []
    found: dict[str, tuple[int, ...]] = {}

    def add_flows(raw: dict[str, Any], path: tuple[int, ...]):
        for phase, field in enumerate(('EnterActions', 'Condition', 'FinishActions')):
            for index, (_, value) in enumerate(walk(raw.get(field))):
                name, flow, state = (value.get(k) for k in ('FlowListName', 'FlowId', 'StateId'))
                if not isinstance(name, str) or type(flow) is not int or type(state) is not int:
                    continue
                state_key = f'{name}_{flow}_{state}'
                # Children use positive path components; finish actions follow
                # the entire subtree rather than preceding the first child.
                order = (*path, 100_001 if field == 'FinishActions' else 0, phase, index)
                if state_key in valid_states and (state_key not in found or order < found[state_key]):
                    found[state_key] = order

    for node in quest_nodes:
        raw = quest_records[node['id']]
        path = tuple(index + 1 for index in node['path'])
        add_flows(raw, path)
        condition = raw.get('Condition')
        ids = condition.get('LevelPlayIds') if isinstance(condition, dict) else None
        if (not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int
                or condition.get('Type') != 'CheckLevelPlay' or condition.get('CompleteNumber') != 1):
            continue
        tree = level_trees.get(ids[0])
        if not tree or not records_match(*tree):
            continue
        level_nodes, level_records = tree
        for level_node in level_nodes:
            add_flows(level_records[level_node['id']],
                      (*path, 1, *(index + 1 for index in level_node['path'])))
    return sorted(found.items(), key=lambda item: (item[1], item[0]))


async def authored_order(session, quest_id: int, release_id: int | None):
    """Resolve an order only when all referenced node records match the client."""
    if release_id is None:
        return None

    async def rows(source: str, identity: int):
        values = await session.scalars(
            select(SourceRecord.data).join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(SourceRecord.release_id == release_id, SourceFile.logical_source_path == source,
                   SourceRecord.data['Key'].astext.like(f'{identity}\\_%', escape='\\'))
        )
        return {int(row['Key'].split('_')[1]): row['Data'] for row in values
                if isinstance(row.get('Data'), dict) and row.get('Key', '').split('_')[-1].isdecimal()}

    candidates = list(await session.scalars(select(AuthoredTreeOrder).where(
        AuthoredTreeOrder.tree_kind == 'quest', AuthoredTreeOrder.tree_id == quest_id)))
    if not candidates:
        return None
    qrecords = await rows('BinData/QuestNodeData/questnodedata.json', quest_id)
    candidates.sort(key=lambda value: (tuple(map(int, value.asset_version.split('.'))), value.download_id), reverse=True)
    quest_tree = next((tree for tree in candidates if records_match(tree.nodes, qrecords)), None)
    if quest_tree is None:
        return None
    level_ids = set()
    for record in qrecords.values():
        condition = record.get('Condition')
        ids = condition.get('LevelPlayIds') if isinstance(condition, dict) else None
        if (condition and condition.get('Type') == 'CheckLevelPlay' and isinstance(ids, list)
                and len(ids) == 1 and type(ids[0]) is int and condition.get('CompleteNumber') == 1):
            level_ids.add(ids[0])
    trees = list(await session.scalars(select(AuthoredTreeOrder).where(
        AuthoredTreeOrder.asset_version == quest_tree.asset_version,
        AuthoredTreeOrder.download_id == quest_tree.download_id,
        AuthoredTreeOrder.tree_kind == 'level_play', AuthoredTreeOrder.tree_id.in_(level_ids)))) if level_ids else []
    level_trees = {tree.tree_id: (tree.nodes, await rows(
        'BinData/LevelPlayNodeData/levelplaynodedata.json', tree.tree_id)) for tree in trees}
    possible = set()
    for record in list(qrecords.values()) + [row for _, records in level_trees.values() for row in records.values()]:
        for _, value in walk(record):
            name, flow, state = (value.get(k) for k in ('FlowListName', 'FlowId', 'StateId'))
            if isinstance(name, str) and type(flow) is int and type(state) is int:
                possible.add(f'{name}_{flow}_{state}')
    states = dict((await session.execute(select(QuestState.state_key, QuestState.node_id)
        .join(NodeRevision, NodeRevision.node_id == QuestState.node_id)
        .where(NodeRevision.release_id == release_id, QuestState.state_key.in_(possible)).distinct())).all())
    positions = flow_positions(quest_tree.nodes, qrecords, level_trees, set(states))
    if not positions:
        return None
    ids = [states[key] for key, _ in positions]
    actions = list(await session.execute(select(QuestAction.node_id, QuestAction.quest_state_node_id)
                                        .where(QuestAction.quest_state_node_id.in_(ids))))
    memberships = set(await source_parents(session, {a for a, _ in actions}, ('contains_action',), release_id))
    movies = await media_links(session, [a for a, s in actions if (a, s) in memberships], ('plays_cutscene',), release_id)
    cutscene_ids = {link['node_id'] for values in movies.values() for link in values}
    subtitles = await media_links(session, list(cutscene_ids), ('has_transcript_state',), release_id)
    captions_by_state: dict[int, list[int]] = {}
    for action, state in actions:
        if (action, state) not in memberships:
            continue
        for scene in movies.get(action, []):
            captions_by_state.setdefault(state, []).extend(
                link['node_id'] for link in subtitles.get(scene['node_id'], []))
    ordered: list[int] = []
    for state in ids:
        for identity in (state, *captions_by_state.get(state, [])):
            if identity not in ordered:
                ordered.append(identity)
    return {'state_ids': ordered, 'asset_version': quest_tree.asset_version,
            'mapped_flow_states': [key for key, _ in positions]}
