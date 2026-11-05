"""Gameplay objectives and media retain authored branch paths, not a linear run."""

from wuwa_story.ingestion.dungeon_context import decoded, walk
from wuwa_story.ingestion.runtime_media import runtime_media
from wuwa_story.ingestion.spine_presentation import flow_presentation

BRANCH_TYPES = {'ConditionSelector', 'ParallelSelect', 'Parallel', 'Selector', 'AlwaysFalse'}
LABELS = {'ReachArea': 'Explore the marked area', 'DoInteract': 'Interact with the surroundings',
          'CheckLevelPlay': 'Explore the area', 'FinishDungeon': 'Complete the area',
          'EnterDungeon': 'Enter the area', 'LeaveDungeon': 'Leave the area',
          'ReadPhoneMessage': 'Read the message', 'WaitTime': 'Wait',
          'WaitBattleCondition': 'Complete the encounter', 'Kill': 'Defeat the target',
          'PlayFlow': 'Continue the story'}


def dungeon_requirement(raw):
    """A single explicit dungeon completion; not a prerequisite or a name match."""
    condition = decoded(raw.get('Condition'))
    target = condition.get('Condition') if isinstance(condition, dict) else None
    if (isinstance(condition, dict) and condition.get('Type') == 'FinishDungeon' and isinstance(target, dict)
            and target.get('Type') == 'DungeonId' and type(target.get('Count')) is int
            and target['Count'] == 1
            and type(target.get('DungeonId')) is int and target['DungeonId'] > 0):
        return target['DungeonId']
    return None


def cinematic_boundaries(record, field='Actions', *, source=None, prefix='$'):
    """Decoded action boundaries, not playback timestamps or a rendered movie."""
    result = []
    actions = decoded(record.get(field))
    for index, action in enumerate(actions if isinstance(actions, list) else []):
        if not isinstance(action, dict):
            continue
        path = f'{prefix}.{field}[{index}]'
        refs = list(runtime_media(action, path))
        for ref in refs:
            result.append({**ref, 'renderable': False, 'playback_status': 'unresolved',
                'async': action.get('Async'),
                'boundary_semantics': 'authored_action_extent',
                'start': {'action_index': index, 'anchor': 'before_action'},
                'end': {'action_index': index, 'anchor': 'after_action'},
                'source': {**(source or {'file': 'BinData/flowState/flowstate.json',
                                        'key': record.get('StateKey'), **record.get('_source', {})}),
                           'raw_path': ref['raw_path']}})
    return result


def encounter_tree(identity, nodes, records, occurrence, flow_records, state_ids):
    """Local child paths preserve alternatives; never export a global timeline."""
    if any(not isinstance(node, dict) or type(node.get('id')) is not int
           or not isinstance(node.get('type'), str) for node in nodes):
        raise ValueError('invalid_authored_tree_metadata')
    by_id = {node['id']: node for node in nodes}
    roots = [node for node in nodes if node.get('parent_id') == 0 and node.get('path') == []]
    if len(roots) != 1 or len(by_id) != len(nodes):
        raise ValueError('missing_or_ambiguous_authored_root')
    paths = set()
    for node in nodes:
        path = node.get('path')
        if (not isinstance(path, list) or any(type(index) is not int or index < 0 for index in path)
                or tuple(path) in paths or not isinstance(records.get(node['id']), dict)):
            raise ValueError('invalid_authored_tree_topology')
        paths.add(tuple(path))
        if node is not roots[0]:
            parent = by_id.get(node.get('parent_id'))
            if parent is None or not path or parent.get('path') != path[:-1]:
                raise ValueError('invalid_authored_tree_topology')
    built = {}
    for node in sorted(nodes, key=lambda node: node['path']):
        raw = records[node['id']]
        condition = decoded(raw.get('Condition'))
        owner = f'level_play_node:{identity}:{node["id"]}'
        entry = {'id': f'{occurrence}/{owner}', 'owner': owner, 'node_type': node['type'],
            'path': node['path'], 'children_order': {
                'Sequence': 'sequence', 'ParallelSelect': 'parallel',
                'ConditionSelector': 'conditional', 'Select': 'alternative',
                'AlwaysFalse': 'decorator', 'AlwaysTrue': 'decorator',
            }.get(node['type'], 'unknown'),
            'title': LABELS.get(condition.get('Type') if isinstance(condition, dict) else None,
                                'Story activity'),
            'text_key': raw.get('TidTip'),
            'condition': condition, 'count': raw.get('Count'),
            'flow_states': [], 'children': [], 'media': [], 'completions': [],
            'action_sections': [],
            'source': {'file': 'BinData/LevelPlayNodeData/levelplaynodedata.json',
                       'key': f'{identity}_{node["id"]}', 'raw_path': '$.Data'}}
        sections = [(field, raw, field, '$.Data', 'authored')
                    for field in ('EnterActions', 'Condition', 'Actions', 'FinishActions')]
        save = decoded(raw.get('SaveConfig'))
        if isinstance(save, dict):
            # The source names this lifecycle section, but does not prove when it
            # executes relative to normal entry, combat, or reconnect playback.
            sections.extend((f'SaveConfig.{field}', save, field, '$.Data.SaveConfig', 'save_config')
                            for field in ('EnterActions', 'Actions', 'FinishActions'))
        for phase, section, field, prefix, context in sections:
            section_path = f'{prefix}.{field}'
            entry['media'].extend({**ref, 'phase': phase, 'execution_context': context}
                for ref in cinematic_boundaries(section, field, source=entry['source'], prefix=prefix))
            actions = decoded(section.get(field))
            if field != 'Condition' and actions is not None:
                entry['action_sections'].append({'phase': phase, 'execution_context': context,
                    'runtime_order': 'unknown' if context == 'save_config' else 'authored_array',
                    'source': {**entry['source'], 'raw_path': section_path},
                    'status': 'decoded' if isinstance(actions, list) else 'unsupported_shape',
                    'unprojected_actions': [
                        {'index': index, 'name': action.get('Name') if isinstance(action, dict) else None}
                        for index, action in enumerate(actions if isinstance(actions, list) else [])
                        if not isinstance(action, dict) or (action.get('Name') not in ('PlayFlow', 'FinishDungeon')
                            and not list(runtime_media(action)))]})
            for path, action in walk(section.get(field), section_path):
                if (action.get('Name') == 'FinishDungeon' and isinstance(action.get('Params'), dict)
                        and type(action['Params'].get('IsSuccess')) is bool):
                    entry['completions'].append({'is_success': action['Params']['IsSuccess'],
                        'phase': phase, 'execution_context': context,
                        'source': {**entry['source'], 'raw_path': path + '.Params.IsSuccess'}})
                if phase == 'Condition' and action.get('Type') == 'PlayFlow':
                    params, reference_field, asynchronous = decoded(action.get('Flow')), 'Flow', False
                elif action.get('Name') == 'PlayFlow':
                    params, reference_field, asynchronous = decoded(action.get('Params')), 'Params', action.get('Async')
                else:
                    continue
                if not isinstance(params, dict):
                    continue
                name, flow, state = (params.get(key) for key in ('FlowListName', 'FlowId', 'StateId'))
                if not isinstance(name, str) or type(flow) is not int or type(state) is not int:
                    continue
                key = f'{name}_{flow}_{state}'
                flow_record = flow_records.get(key)
                presentation = flow_presentation({key: value for key, value in flow_record.items()
                    if key != '_source'}) if flow_record else None
                entry['flow_states'].append({'id': f'{entry["id"]}:{path}', 'state': key,
                    'presentation_sha256': presentation['sha256'] if presentation else None,
                    'state_node_id': state_ids.get(key), 'phase': phase, 'async': asynchronous,
                    'execution_context': context,
                    'timing': 'unknown' if context == 'save_config' else
                              'during' if asynchronous is True else
                              'blocking' if asynchronous is False else 'unknown',
                    'cinematics': cinematic_boundaries(flow_records.get(key, {})),
                    'source': {**entry['source'], 'raw_path': path + '.' + reference_field}})
        built[node['id']] = entry
        parent = built.get(node['parent_id'])
        if parent:
            if records[node['parent_id']]['Type'] == 'ConditionSelector':
                slots = records[node['parent_id']].get('Slots', [])
                slot = next((slot for slot in slots if slot.get('Node', {}).get('Id') == node['id']), {})
                entry['branch_condition'] = slot.get('Condition')
            parent['children'].append(entry)
    return built[roots[0]['id']]


def project_blocks(quest_id, quest_nodes, quest_records, level_trees, level_interactions=None,
                   *, dungeon_memberships=None, flow_records=None, state_ids=None, release_id=None,
                   client=None, dungeon_instances=None):
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
            dungeon = dungeon_requirement(raw) if kind == 'quest_node' else None
            encounter = None
            transition = None
            instance = (dungeon_instances or {}).get(dungeon)
            target = condition.get('Condition')
            if (dungeon is not None and isinstance(target, dict) and target.get('IsExitDungeon') is True
                    and instance and type(instance.get('map_config_id')) is int
                    and instance['map_config_id'] > 0 and instance['map_config_id'] != dungeon):
                # An exit from another instance of the same map does not replay
                # its encounter. Keep the instance and map identities separate.
                transition = {'id': f'{owner}@{occurrence}/dungeon_exit:{dungeon}',
                    'kind': 'dungeon_exit', 'dungeon_id': dungeon,
                    'map_config_id': instance['map_config_id'], 'is_exit_dungeon': True,
                    'count': target['Count'], 'status': 'verified',
                    'boundary_semantics': 'authored_exit_requirement',
                    'source': {'file': 'BinData/QuestNodeData/questnodedata.json',
                               'key': f'{identity}_{node["id"]}', 'raw_path': '$.Data.Condition.Condition'},
                    'instance_source': instance['source']}
            if dungeon is not None and transition is None:
                trees = []
                unresolved = []
                battle = False
                for level_id, membership in sorted((dungeon_memberships or {}).items()):
                    if membership['dungeon_id'] != dungeon:
                        continue
                    if level_id not in level_trees:
                        unresolved.append({'level_play_id': level_id, 'membership': membership,
                                           'reason': 'matching_authored_tree_unavailable'})
                        continue
                    child_nodes, child_records = level_trees[level_id]
                    try:
                        root = encounter_tree(level_id, child_nodes, child_records,
                            f'{owner}@{occurrence}', flow_records or {}, state_ids or {})
                    except ValueError as error:
                        unresolved.append({'level_play_id': level_id, 'membership': membership,
                                           'reason': 'invalid_authored_tree', 'diagnostic': str(error)})
                        continue
                    battle |= any(isinstance(battle_condition := decoded(record.get('Condition')), dict)
                                  and battle_condition.get('Type') in {'WaitBattleCondition', 'Kill'}
                                  for record in child_records.values())
                    trees.append({'level_play_id': level_id, 'ordering': 'authored_tree',
                        'membership': membership,
                        'root': root})
                encounter = {'id': f'{owner}@{occurrence}/dungeon:{dungeon}',
                    'kind': 'encounter', 'dungeon_id': dungeon,
                    'title': 'Battle' if battle else 'Encounter',
                    'release_id': release_id, 'client': client,
                    'status': ('partial' if unresolved else 'verified') if trees else 'unresolved',
                    'unresolved_level_plays': unresolved,
                    'trees_order': 'unknown', 'trees': trees,
                    'source': {'file': 'BinData/QuestNodeData/questnodedata.json',
                               'key': f'{identity}_{node["id"]}', 'raw_path': '$.Data.Condition'}}
            if raw.get('TidTip') or media or interactions or encounter or transition:
                block = {'id': f'{owner}@{occurrence}', 'owner': owner,
                    'kind': 'objective', 'condition_type': condition.get('Type'),
                    'text_key': raw.get('TidTip'),
                    'title': 'Leave the area' if transition else LABELS.get(condition.get('Type'), 'Story activity'),
                    'position': [*path, 0, 1, -1], 'branches': branches,
                    'flow_states': flows, 'media': media, 'interactions': interactions,
                    'source': {'file': 'BinData/QuestNodeData/questnodedata.json' if kind == 'quest_node'
                               else 'BinData/LevelPlayNodeData/levelplaynodedata.json',
                               'key': f'{identity}_{node["id"]}', 'raw_path': '$.Data'}}
                if encounter:
                    block['encounter'] = encounter
                if transition:
                    block['transition'] = transition
                result.append(block)
            if (condition.get('Type') == 'CheckLevelPlay' and isinstance(levels, list)
                    and len(levels) == 1 and type(levels[0]) is int and levels[0] not in active
                    and condition.get('CompleteNumber') == 1 and levels[0] in level_trees):
                children, child_records = level_trees[levels[0]]
                visit(children, child_records, 'level_play_node', levels[0], (*path, 1), tuple(branches), (*active, levels[0]))

    visit(quest_nodes, quest_records, 'quest_node', quest_id)
    return sorted(result, key=lambda block: (block['position'], block['id']))
