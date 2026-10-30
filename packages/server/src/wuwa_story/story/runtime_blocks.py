"""Gameplay objectives and media retain authored branch paths, not a linear run."""

from wuwa_story.ingestion.dungeon_context import decoded, walk
from wuwa_story.ingestion.runtime_media import runtime_media

BRANCH_TYPES = {'ConditionSelector', 'ParallelSelect', 'Parallel', 'Selector', 'AlwaysFalse'}
LABELS = {'ReachArea': 'Explore the marked area', 'DoInteract': 'Interact with the surroundings',
          'CheckLevelPlay': 'Explore the area', 'FinishDungeon': 'Complete the area',
          'EnterDungeon': 'Enter the area', 'LeaveDungeon': 'Leave the area',
          'ReadPhoneMessage': 'Read the message', 'WaitTime': 'Wait',
          'WaitBattleCondition': 'Complete the encounter', 'PlayFlow': 'Continue the story'}


def project_blocks(quest_id, quest_nodes, quest_records, level_trees, level_interactions=None):
    """Called only after tree/record hashes match. Stable IDs include occurrence."""
    result = []

    def visit(nodes, records, kind, identity, prefix=(), inherited=(), active=()):
        by_id = {node['id']: node for node in nodes}
        for node in nodes:
            raw = records[node['id']]
            path = (*prefix, *(index + 1 for index in node['path']))
            branches = list(inherited)
            parent = by_id.get(node['parent_id'])
            ancestors = []
            while parent:
                if parent['type'] in BRANCH_TYPES:
                    ancestors.append({'id': f'{kind}:{identity}:{parent["id"]}', 'type': parent['type']})
                parent = by_id.get(parent['parent_id'])
            branches.extend(reversed(ancestors))
            owner = f'{kind}:{identity}:{node["id"]}'
            occurrence = ':'.join(map(str, path))
            condition = decoded(raw.get('Condition'))
            condition = condition if isinstance(condition, dict) else {}
            levels = condition.get('LevelPlayIds')
            interactions = ((level_interactions or {}).get(levels[0], [])
                            if condition.get('Type') == 'CheckLevelPlay' and isinstance(levels, list)
                            and len(levels) == 1 and condition.get('CompleteNumber') == 1 else [])
            flows = []
            media = []
            for phase, field in enumerate(('EnterActions', 'Condition', 'FinishActions')):
                phase_path = (*path, 100_001 if field == 'FinishActions' else 0, phase)
                for index, (raw_path, obj) in enumerate(walk(raw.get(field), f'$.Data.{field}')):
                    name, flow, state = (obj.get(key) for key in ('FlowListName', 'FlowId', 'StateId'))
                    if isinstance(name, str) and type(flow) is int and type(state) is int:
                        flows.append({'state': f'{name}_{flow}_{state}', 'position': [*phase_path, index]})
                    for ref in runtime_media(obj, raw_path, recursive=False):
                        media.append({**ref, 'position': [*phase_path, index]})
            if raw.get('TidTip') or media or interactions:
                result.append({'id': f'{owner}@{occurrence}', 'owner': owner,
                    'kind': 'objective', 'condition_type': condition.get('Type'),
                    'text_key': raw.get('TidTip'),
                    'title': LABELS.get(condition.get('Type'), 'Story activity'),
                    'position': [*path, 0, 1, -1], 'branches': branches,
                    'flow_states': flows, 'media': media, 'interactions': interactions,
                    'source': {'file': 'BinData/QuestNodeData/questnodedata.json' if kind == 'quest_node'
                               else 'BinData/LevelPlayNodeData/levelplaynodedata.json',
                               'key': f'{identity}_{node["id"]}', 'raw_path': '$.Data'}})
            if (condition.get('Type') == 'CheckLevelPlay' and isinstance(levels, list)
                    and len(levels) == 1 and type(levels[0]) is int and levels[0] not in active
                    and condition.get('CompleteNumber') == 1 and levels[0] in level_trees):
                children, child_records = level_trees[levels[0]]
                visit(children, child_records, 'level_play_node', levels[0], (*path, 1), tuple(branches), (*active, levels[0]))

    visit(quest_nodes, quest_records, 'quest_node', quest_id)
    return sorted(result, key=lambda block: (block['position'], block['id']))
