"""Project source-authored tree positions onto quest and LevelPlay flow states."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from wuwa_story.db.models.core import AuthoredTreeOrder, QuestAction, QuestState
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.cutscenes import source_parents
from wuwa_story.ingestion.dungeon_context import DUNGEONS, level_memberships, walk
from wuwa_story.ingestion.level_interactions import verified_interactions
from wuwa_story.ingestion.quest_tree_order import records_match
from wuwa_story.story.media import media_links
from wuwa_story.story.runtime_blocks import dungeon_requirement, project_blocks


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
                name, flow, state = (value.get(k) for k in (
                    'FlowListName', 'FlowId', 'StateId'))
                if not isinstance(name, str) or type(flow) is not int or type(state) is not int:
                    continue
                state_key = f'{name}_{flow}_{state}'
                # Children use positive path components; finish actions follow
                # the entire subtree rather than preceding the first child.
                order = (*path, 100_001 if field ==
                         'FinishActions' else 0, phase, index)
                if state_key in valid_states and (state_key not in found or order < found[state_key]):
                    found[state_key] = order

    for node in quest_nodes:
        raw = quest_records[node['id']]
        path = tuple(index + 1 for index in node['path'])
        add_flows(raw, path)
        condition = raw.get('Condition')
        ids = condition.get('LevelPlayIds') if isinstance(
            condition, dict) else None
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
    """Resolve full order, or independently verified encounters without quest positions."""
    if release_id is None:
        return None

    async def rows(source: str, identity: int):
        values = await session.scalars(
            select(SourceRecord.data).join(
                SourceFile, SourceFile.id == SourceRecord.source_file_id)
            .where(SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
                   SourceFile.logical_source_path == source,
                   SourceRecord.data['Key'].astext.like(f'{identity}\\_%', escape='\\'))
        )
        return {int(row['Key'].split('_')[1]): row['Data'] for row in values
                if isinstance(row.get('Data'), dict) and row.get('Key', '').split('_')[-1].isdecimal()}

    candidates = list(await session.scalars(select(AuthoredTreeOrder).where(
        AuthoredTreeOrder.tree_kind == 'quest', AuthoredTreeOrder.tree_id == quest_id)))
    if not candidates:
        return None
    qrecords = await rows('BinData/QuestNodeData/questnodedata.json', quest_id)
    candidates.sort(key=lambda value: (tuple(
        map(int, value.asset_version.split('.'))), value.download_id), reverse=True)
    quest_tree = next(
        (tree for tree in candidates if records_match(tree.nodes, qrecords)), None)
    related = quest_tree is None
    quest_nodes = quest_tree.nodes if quest_tree else []
    if related:
        # A changed quest cannot lend its paths/ancestors to an older snapshot.
        # Its unchanged dungeon owners can still identify complete matching
        # LevelPlay trees. Each owner is detached; only local LevelPlay order survives.
        for tree in candidates:
            matched = [node for node in tree.nodes if node['id'] in qrecords
                       and dungeon_requirement(qrecords[node['id']]) is not None
                       and records_match([node], {node['id']: qrecords[node['id']]})]
            if matched:
                quest_tree = tree
                quest_nodes = [{**node, 'path': []} for node in matched]
                qrecords = {node['id']: qrecords[node['id']] for node in matched}
                break
    if quest_tree is None:
        return None
    level_ids = set()
    dungeon_ids = {identity for record in qrecords.values()
                   if (identity := dungeon_requirement(record)) is not None}
    dungeon_memberships = {}
    if dungeon_ids:
        memberships = await session.execute(select(SourceRecord.row_index, SourceRecord.data,
                                                   SourceRecord.id).join(SourceFile).where(
            SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
            SourceFile.logical_source_path == 'BinData/LevelPlayData/levelplaydata.json',
            SourceRecord.data['Data']['LevelId'].as_integer().in_(dungeon_ids)))
        for row_index, data, record_id in memberships:
            for identity, membership in level_memberships([(row_index, data)], dungeon_ids).items():
                dungeon_memberships[identity] = {**membership, 'record_id': record_id,
                                                 'release_id': release_id, 'raw_path': f'$[{row_index}].Data.LevelId'}
        level_ids.update(dungeon_memberships)
    # Resolve only exit requirements without direct LevelPlay membership. Map
    # identity is evidence for an exit marker, never an alias for battle trees.
    exit_ids = {dungeon_requirement(record) for record in qrecords.values()
                if isinstance(record.get('Condition'), dict)
                and isinstance(record['Condition'].get('Condition'), dict)
                and record['Condition']['Condition'].get('IsExitDungeon') is True} - {None}
    exit_ids -= {membership['dungeon_id'] for membership in dungeon_memberships.values()}
    dungeon_instances = {}
    if exit_ids:
        instance_rows = await session.execute(select(SourceRecord.row_index, SourceRecord.data,
                                                     SourceRecord.id).join(SourceFile).where(
            SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
            SourceFile.logical_source_path == DUNGEONS,
            SourceRecord.data['Id'].as_integer().in_(exit_ids)))
        seen_instances = set()
        for row_index, data, record_id in instance_rows:
            identity, map_id = data.get('Id'), data.get('MapConfigId')
            if type(identity) is not int or identity not in exit_ids:
                continue
            if identity in seen_instances:
                dungeon_instances.pop(identity, None)
                continue
            seen_instances.add(identity)
            if type(map_id) is int and map_id > 0:
                dungeon_instances[identity] = {'map_config_id': map_id, 'source': {
                    'file': DUNGEONS, 'key': identity, 'record_id': record_id,
                    'row': row_index, 'release_id': release_id, 'raw_path': f'$[{row_index}].MapConfigId'}}
    for record in qrecords.values():
        condition = record.get('Condition')
        ids = condition.get('LevelPlayIds') if isinstance(
            condition, dict) else None
        if (condition and condition.get('Type') == 'CheckLevelPlay' and isinstance(ids, list)
                and len(ids) == 1 and type(ids[0]) is int and condition.get('CompleteNumber') == 1):
            level_ids.add(ids[0])
    trees = list(await session.scalars(select(AuthoredTreeOrder).where(
        AuthoredTreeOrder.asset_version == quest_tree.asset_version,
        AuthoredTreeOrder.download_id == quest_tree.download_id,
        AuthoredTreeOrder.tree_kind == 'level_play', AuthoredTreeOrder.tree_id.in_(level_ids)))) if level_ids else []
    level_trees = {tree.tree_id: (tree.nodes, await rows(
        'BinData/LevelPlayNodeData/levelplaynodedata.json', tree.tree_id)) for tree in trees}
    level_trees = {identity: tree for identity,
                   tree in level_trees.items() if records_match(*tree)}
    level_rows = list(await session.scalars(select(SourceRecord.data).join(SourceFile).where(
        SourceRecord.release_id == release_id,
        SourceFile.release_id == release_id,
        SourceFile.logical_source_path == 'BinData/LevelPlayData/levelplaydata.json',
        SourceRecord.data['LevelPlayId'].as_integer().in_(level_trees)))) if level_trees else []
    interactions = {row['LevelPlayId']: verified_interactions(level_trees[row['LevelPlayId']][0], row['Data'])
                    for row in level_rows if isinstance(row.get('Data'), dict)}
    possible = set()
    optional_states = {flow['state'] for items in interactions.values(
    ) for item in items for flow in item['flow_states']}
    possible.update(optional_states)
    for record in list(qrecords.values()) + [row for _, records in level_trees.values() for row in records.values()]:
        for _, value in walk(record):
            name, flow, state = (value.get(k)
                                 for k in ('FlowListName', 'FlowId', 'StateId'))
            if isinstance(name, str) and type(flow) is int and type(state) is int:
                possible.add(f'{name}_{flow}_{state}')
    states = dict((await session.execute(select(QuestState.state_key, QuestState.node_id)
                                         .join(NodeRevision, NodeRevision.node_id == QuestState.node_id)
                                         .where(NodeRevision.release_id == release_id, QuestState.state_key.in_(possible)).distinct())).all())
    flow_records = {}
    if dungeon_memberships and possible:
        flow_rows = await session.execute(select(SourceRecord.data, SourceRecord.id,
                                                 SourceRecord.row_index).join(SourceFile).where(
            SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
            SourceFile.logical_source_path == 'BinData/flowState/flowstate.json',
            SourceRecord.data['StateKey'].astext.in_(possible)))
        for data, record_id, row_index in flow_rows:
            flow_records[data['StateKey']] = {**data, '_source': {
                'record_id': record_id, 'row': row_index, 'release_id': release_id}}
    blocks = project_blocks(quest_id, quest_nodes, qrecords, level_trees, interactions,
                            dungeon_memberships=dungeon_memberships, flow_records=flow_records,
                            state_ids=states, release_id=release_id,
                            dungeon_instances=dungeon_instances,
                            client={'asset_version': quest_tree.asset_version,
                                    'download_id': quest_tree.download_id})
    if related:
        blocks = [block for block in blocks if block.get('encounter', {}).get('trees') or block.get('transition')]
        if not blocks:
            return None
        source_rows = await session.execute(select(SourceRecord.id, SourceRecord.row_index,
                                                   SourceRecord.data['Key'].astext).join(SourceFile).where(
            SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
            SourceFile.logical_source_path == 'BinData/QuestNodeData/questnodedata.json',
            SourceRecord.data['Key'].astext.in_([block['source']['key'] for block in blocks])))
        sources = {key: {'record_id': identity, 'row': row, 'release_id': release_id}
                   for identity, row, key in source_rows}
        state_ids = set()
        for block in blocks:
            block.update(position=None, branches=[], ordering='related',
                         flow_states=[], media=[], interactions=[])
            block['source'].update(sources[block['source']['key']])
            if block.get('transition'):
                block['transition']['source'].update(sources[block['source']['key']])
            encounter = block.get('encounter')
            if not encounter:
                continue
            encounter.update(quest_position=None, quest_order='unknown')
            encounter['source'].update(sources[block['source']['key']])
            pending = [tree['root'] for tree in encounter['trees']]
            while pending:
                node = pending.pop()
                state_ids.update(flow['state_node_id'] for flow in node['flow_states']
                                 if flow.get('state_node_id') is not None)
                pending.extend(node['children'])
        return {'state_ids': sorted(state_ids), 'asset_version': quest_tree.asset_version,
                'ordering': 'related', 'mapped_flow_states': [], 'positions': {}, 'blocks': blocks}
    positions = flow_positions(
        quest_tree.nodes, qrecords, level_trees, set(states))
    if not positions and not blocks:
        return None
    ids = [states[key] for key, _ in positions]
    actions = list(await session.execute(select(QuestAction.node_id, QuestAction.quest_state_node_id, QuestAction.action_index)
                                         .where(QuestAction.quest_state_node_id.in_(ids))))
    memberships = set(await source_parents(session, {a for a, _, _ in actions}, ('contains_action',), release_id))
    movies = await media_links(session, [a for a, s, _ in actions if (a, s) in memberships], ('plays_cutscene',), release_id)
    cutscene_ids = {link['node_id']
                    for values in movies.values() for link in values}
    subtitles = await media_links(session, list(cutscene_ids), ('has_transcript_state',), release_id)
    captions_by_state: dict[int, list[int]] = {}
    caption_positions = {}
    positions_by_id = {states[key]: position for key, position in positions}
    for action, state, action_index in actions:
        if (action, state) not in memberships:
            continue
        for scene in movies.get(action, []):
            for link in subtitles.get(scene['node_id'], []):
                captions_by_state.setdefault(state, []).append(link['node_id'])
                position = (*positions_by_id[state], action_index, 0)
                caption_positions[link['node_id']] = min(
                    caption_positions.get(link['node_id'], position), position)
    ordered: list[int] = []
    for state in ids:
        for identity in (state, *captions_by_state.get(state, [])):
            if identity not in ordered:
                ordered.append(identity)
    for key in sorted(optional_states):
        if key in states and states[key] not in ordered:
            ordered.append(states[key])
    # Fetch every source-backed encounter state even on imports without compiled
    # dungeon ownership edges. This is deterministic enumeration, not a timeline:
    # only flat positions above establish a global display position.
    pending = [tree['root'] for block in blocks
               for tree in block.get('encounter', {}).get('trees', [])][::-1]
    while pending:
        node = pending.pop()
        for flow in node['flow_states']:
            if (identity := flow.get('state_node_id')) is not None and identity not in ordered:
                ordered.append(identity)
        pending.extend(reversed(node['children']))
    caption_keys = dict((await session.execute(select(QuestState.node_id, QuestState.state_key).where(
        QuestState.node_id.in_(caption_positions)))).all()) if caption_positions else {}
    all_positions = {**dict(positions), **{caption_keys[identity]: position for identity, position in caption_positions.items()
                                           if identity in caption_keys}}
    return {'state_ids': ordered, 'asset_version': quest_tree.asset_version,
            'mapped_flow_states': [key for key, _ in positions],
            'positions': all_positions, 'blocks': blocks}
