"""Explicit LevelPlay entity membership and narrative interaction references."""

import hashlib
import json
import re

from wuwa_story.ingestion.dungeon_context import decoded, walk
from wuwa_story.ingestion.runtime_media import runtime_media


def membership_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def entity_members(data):
    return {value for field in ('Reference', 'Children')
            for value in (data.get(field) or [])
            if isinstance(value, str) and re.fullmatch(r'e_\d+_\d+', value)}


def interaction_options(map_id, entity_id, components, source):
    component = components.get('InteractComponent')
    if not isinstance(component, dict):
        return
    for index, option in enumerate(component.get('Options') or []):
        if not isinstance(option, dict):
            continue
        flows = []
        for path, value in walk(option):
            field = 'Params' if value.get('Name') == 'PlayFlow' else 'Flow' if value.get('Type') == 'Flow' else None
            params = decoded(value.get(field)) if field else None
            if not isinstance(params, dict):
                continue
            name, flow, state = (params.get(key) for key in ('FlowListName', 'FlowId', 'StateId'))
            if isinstance(name, str) and type(flow) is int and type(state) is int:
                flows.append({'state': f'{name}_{flow}_{state}', 'raw_path': path + '.' + field})
        media = list(runtime_media(option))
        if not flows and not media and not option.get('TidContent'):
            continue
        yield {'id': f'e_{map_id}_{entity_id}:{index}', 'entity': f'e_{map_id}_{entity_id}',
               'text_key': option.get('TidContent'), 'title': 'Inspect the surroundings',
               'flow_states': flows, 'media': media,
               'conditions': option.get('Condition') or option.get('Conditions'),
               'source': {**source, 'raw_path': f'$.ComponentsData.InteractComponent.Options[{index}]'}}


def verified_interactions(nodes, level_data):
    """A newer client must not silently change a historical scenario's membership."""
    root = next((node for node in nodes if node.get('path') == []), {})
    if root.get('interaction_membership_sha256') != membership_hash(level_data):
        return []
    members = entity_members(level_data)
    return [item for item in root.get('interactions', []) if item.get('entity') in members]


def interaction_links(quest_rows, level_rows, indexes, valid_states):
    levels = {row['LevelPlayId']: decoded(row.get('Data')) for _, row in level_rows}
    for row_index, row in quest_rows:
        data = decoded(row.get('Data'))
        condition = data.get('Condition') if isinstance(data, dict) else None
        ids = condition.get('LevelPlayIds') if isinstance(condition, dict) else None
        if (not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int
                or condition.get('Type') != 'CheckLevelPlay' or condition.get('CompleteNumber') != 1):
            continue
        identity = ids[0]
        if not isinstance(levels.get(identity), dict):
            continue
        parts = row.get('Key', '').split('_')
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            continue
        for item in verified_interactions(indexes.get(identity, []), levels[identity]):
            for flow in item['flow_states']:
                if flow['state'] in valid_states:
                    yield {'from': f'quest_node:{parts[0]}:{parts[1]}',
                           'to': f'flow_state:{flow["state"]}',
                           'source': item['source']['file'],
                           'raw_path': f'levelentityconfig[Id={item["source"]["key"]}]'
                                       + item['source']['raw_path'][1:] + flow['raw_path'][1:],
                           'basis': json.dumps({'meaning': 'Explicit scenario entity interaction; not mandatory playthrough order',
                                               'level_play_id': identity, 'entity': item['entity'],
                                               'quest_condition': {'file': 'BinData/QuestNodeData/questnodedata.json',
                                                                   'row': row_index, 'key': row['Key']},
                                               'client_source': item['source'], 'flow': flow},
                                              sort_keys=True, ensure_ascii=False)}


def required_level_links(quest_rows, level_rows, verified_trees, valid_states):
    """Exact required-scenario ownership, independent of which branch was played."""
    requirements = {}
    for index, row in quest_rows:
        data = decoded(row.get('Data'))
        condition = data.get('Condition') if isinstance(data, dict) else None
        ids = condition.get('LevelPlayIds') if isinstance(condition, dict) else None
        parts = row.get('Key', '').split('_')
        if (isinstance(ids, list) and len(ids) == 1 and type(ids[0]) is int
                and condition.get('Type') == 'CheckLevelPlay' and condition.get('CompleteNumber') == 1
                and ids[0] in verified_trees and len(parts) == 2 and all(part.isdecimal() for part in parts)):
            requirements.setdefault(ids[0], []).append((index, row['Key'], f'quest_node:{parts[0]}:{parts[1]}'))
    for index, row in level_rows:
        parts = row.get('Key', '').split('_')
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            continue
        for path, value in walk(row.get('Data'), f'$[{index}].Data'):
            name, flow, state = (value.get(key) for key in ('FlowListName', 'FlowId', 'StateId'))
            if not isinstance(name, str) or type(flow) is not int or type(state) is not int:
                continue
            state_key = f'{name}_{flow}_{state}'
            if state_key not in valid_states:
                continue
            for quest_row, quest_key, owner in requirements.get(int(parts[0]), []):
                yield {'from': owner, 'to': f'flow_state:{state_key}',
                       'source': 'BinData/LevelPlayNodeData/levelplaynodedata.json', 'raw_path': path,
                       'basis': json.dumps({'meaning': 'Required authored LevelPlay scenario; branch occurrence is not guaranteed',
                                           'level_play_id': int(parts[0]), 'level_play_node': row['Key'],
                                           'quest_condition': {'file': 'BinData/QuestNodeData/questnodedata.json',
                                                               'row': quest_row, 'key': quest_key}},
                                          sort_keys=True, ensure_ascii=False)}
