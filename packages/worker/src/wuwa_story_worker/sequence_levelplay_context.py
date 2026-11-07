"""Resolve source-backed LevelPlay soundtrack candidates from one pinned client."""

import asyncio
import hashlib
import re
from pathlib import Path

from wuwa_story.ingestion.quest_tree_order import read_tree_node_record, read_tree_order
from wuwa_story.ingestion.spine_presentation import presentation_hash

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.map_sources import open_db


def _event(action):
    if not isinstance(action, dict) or action.get('Name') != 'PostAkEvent':
        return None
    params = action.get('Params')
    config = params.get('EventConfig') if isinstance(params, dict) else None
    path = config.get('AkEvent') if isinstance(config, dict) else None
    if (not isinstance(path, str) or not re.fullmatch(r'/Game/Aki/WwiseAudio/Events/[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?', path)):
        return None
    return path


def _actions(record, field, prefix):
    actions = record.get(field)
    if not isinstance(actions, list):
        return []
    result = []
    for index, action in enumerate(actions):
        path = _event(action)
        if path and path.rsplit('/', 1)[-1].split('.')[0].lower().startswith(prefix):
            result.append(
                (path, f'$.Data.{field}[{index}].Params.EventConfig.AkEvent'))
    return result


def find_candidates(db_path, state_key):
    """Find exact native PlayFlow owners; tree membership is validated separately."""
    if not isinstance(state_key, str) or not state_key:
        return []
    found = []
    with open_db(Path(db_path)) as db:
        for key, blob in db.execute('SELECT Key, BinData FROM levelplaynodedata'):
            decoded_key, record = read_tree_node_record(blob)
            if decoded_key != key:
                raise ValueError('LevelPlay node key mismatch')
            condition = record.get('Condition')
            flow = condition.get('Flow') if isinstance(
                condition, dict) and condition.get('Type') == 'PlayFlow' else None
            if not isinstance(flow, dict):
                continue
            name, flow_id, state = (flow.get(k)
                                    for k in ('FlowListName', 'FlowId', 'StateId'))
            if (isinstance(name, str) and type(flow_id) is int and type(state) is int
                    and f'{name}_{flow_id}_{state}' == state_key and re.fullmatch(r'\d+_\d+', key)):
                found.append((key, record, hashlib.sha256(blob).hexdigest()))
    return found


def resolve_context(db_path, tree_path, target, client, candidate):
    """Bind exact authored sibling context; Wwise must independently verify each candidate."""
    key, _, _ = candidate
    level_id, node_id = map(int, key.split('_'))
    records, hashes = {}, {}
    with open_db(Path(db_path)) as db:
        for row_key, blob in db.execute('SELECT Key, BinData FROM levelplaynodedata WHERE Key LIKE ? ESCAPE ?',
                                        (f'{level_id}\\_%', '\\')):
            actual, data = read_tree_node_record(blob)
            if actual != row_key or not row_key.startswith(f'{level_id}_'):
                raise ValueError('LevelPlay node key mismatch')
            identity = row_key.partition('_')[2]
            if not identity.isdecimal() or int(identity) in records:
                raise ValueError('Invalid LevelPlay node identity')
            records[int(identity)] = data
            hashes[int(identity)] = hashlib.sha256(blob).hexdigest()
    tree = Path(tree_path).read_bytes()
    positions = read_tree_order(tree, 'level_play', records, tree_id=level_id)
    nodes = {item.node_id: item for item in positions}
    current = nodes.get(node_id)
    if current is None or not current.path:
        return None
    parent = nodes.get(current.parent_id)
    if parent is None or parent.node_type != 'Sequence':
        return None
    states = _actions(records[node_id], 'EnterActions', 'set_state_')
    stops = _actions(records[node_id], 'FinishActions', 'stop_')
    if len(states) != 1 or len(stops) != 1:
        return None
    siblings = sorted((item for item in positions if item.parent_id == current.parent_id
                       and item.path[:-1] == current.path[:-1] and item.path[-1] < current.path[-1]),
                      key=lambda item: item.path[-1], reverse=True)
    plays, intervening = [], []
    for item in siblings:
        branch = [
            node for node in positions if node.path[:len(item.path)] == item.path]
        intervening.extend(branch)
        if item.node_type != 'ChildQuest':
            # A conditional branch cannot select an unambiguous earlier Play.
            if any(_actions(records[node.node_id], field, 'play_')
                   for node in branch for field in ('EnterActions', 'FinishActions')):
                return None
            continue
        matches = [(item, action) for field in ('FinishActions', 'EnterActions')
                   for action in _actions(records[item.node_id], field, 'play_')]
        if matches:
            plays = matches
            break
    if len(plays) != 1:
        return None
    previous, (play, play_path) = plays[0]

    def proof(identity, path):
        return {'file': 'Client/Content/Aki/ConfigDB/db_LevelPlayNodeData.db',
                'key': f'{level_id}_{identity}', 'sha256': hashes[identity], 'raw_path': path}
    return {'schema_version': 1, 'kind': 'levelplay_fixed_start',
            'play_event': play, 'state_event': states[0][0], 'stop_event': stops[0][0],
            'source_proof': {'asset_version': client['asset_version'], 'download_id': client['download_id'],
                             'release_id': target['release_id'], 'flow_owner': target['owner'],
                             'presentation_sha256': target['presentation_sha256'],
                             'flow_state': target['presentation']['source']['StateKey'],
                             'tree': {'file': f'Client/Content/Aki/Config/UniverseEditorConfig/LevelPlays/{level_id}.bytes',
                                      'sha256': hashlib.sha256(tree).hexdigest(), 'parent_id': parent.node_id,
                                      'play_path': list(previous.path), 'flow_path': list(current.path)},
                             'play': proof(previous.node_id, play_path),
                             'state': proof(node_id, states[0][1]), 'stop': proof(node_id, stops[0][1]),
                             'flow': proof(node_id, '$.Data.Condition.Flow'),
                             'intervening_nodes': [{'key': f'{level_id}_{node.node_id}', 'sha256': hashes[node.node_id],
                                                    'path': list(node.path), 'type': node.node_type}
                                                   for node in intervening if node.node_id != previous.node_id],
                             'timing_basis': 'fixed_start_reconstruction', 'runtime_order_confirmed': False}}


async def resolve_levelplay_context(root, target, fmodel, *, provenance):
    """Return one unambiguous candidate, or None; never infer a Wwise bank match."""
    presentation = target.get('presentation') or {}
    state_key = (presentation.get('source') or {}).get('StateKey')
    if (not state_key or not target.get('presentation_sha256')
            or not provenance.get('asset_version') or not provenance.get('download_id')):
        return None
    if (presentation.get('sha256') != target['presentation_sha256']
            or presentation_hash({key: value for key, value in presentation.items() if key != 'sha256'})
            != target['presentation_sha256']):
        raise ValueError('LevelPlay flow presentation proof mismatch')
    receipt = await export_assets(root, fmodel, 'ConfigDB/db_LevelPlayNodeData.db')
    databases = list(
        (receipt.parent / 'files').rglob('db_LevelPlayNodeData.db'))
    if len(databases) != 1:
        return None
    candidates = await asyncio.to_thread(find_candidates, databases[0], state_key)
    contexts = []
    for candidate in candidates:
        level_id = candidate[0].partition('_')[0]
        tree_receipt = await export_assets(root, fmodel, f'Config/UniverseEditorConfig/LevelPlays/{level_id}.bytes')
        paths = [p for p in (tree_receipt.parent /
                             'files').rglob(f'{level_id}.bytes') if p.is_file()]
        if len(paths) != 1:
            continue
        context = await asyncio.to_thread(resolve_context, databases[0], paths[0], target, provenance, candidate)
        if context:
            contexts.append(context)
    return contexts[0] if len(contexts) == 1 else None
