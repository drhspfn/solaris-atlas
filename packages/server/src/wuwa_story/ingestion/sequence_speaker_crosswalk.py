"""Source-backed speaker identities from sequence actor bindings."""

import hashlib
import json
from collections import Counter, defaultdict

SEQUENCE_ACTOR_BASIS = 'Exact sequence actor model and talker reference: '
FLOW_PATH = 'BinData/flowState/flowstate.json'


def _package(value):
    if isinstance(value, dict):
        value = value.get('ObjectPath')
    if not isinstance(value, str) or not value.startswith('/Game/'):
        return None
    package = value.split('.', 1)[0]
    return package if package != '/Game/' else None


def _properties(exports):
    if not isinstance(exports, list):
        return []
    return [item.get('Properties', {}) for item in exports
            if isinstance(item, dict) and isinstance(item.get('Properties'), dict)]


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def sequence_speaker_links(roles, preloads, flow_states, assets, english):
    """Yield per-scene edges; inputs are source row pairs and decoded asset exports."""
    roles, preloads = list(roles), list(preloads)
    role_counts = Counter(r.get('Id') for _, r in roles if type(r.get('Id')) is int)
    preload_counts = Counter(p.get('Id') for _, p in preloads if type(p.get('Id')) is int)
    preload_by_id = defaultdict(list)
    for row, preload in preloads:
        if type(preload.get('Id')) is int:
            preload_by_id[preload['Id']].append((row, preload))
    names, meshes = defaultdict(list), defaultdict(list)

    def display(key):
        value = english.get(key) if isinstance(key, str) else None
        return value.get('content') if isinstance(value, dict) else None

    for row, role in roles:
        identity, model = role.get('Id'), role.get('MeshId')
        if role.get('RoleType') != 1 or type(identity) is not int:
            continue
        name = display(role.get('Name'))
        if isinstance(name, str) and name:
            names[name].append(identity)
        if type(model) is not int or model not in preload_by_id:
            continue
        for preload_row, preload in preload_by_id[model]:
            paths = preload.get('Meshes')
            if not isinstance(paths, list):
                continue
            for path in {_package(path) for path in paths} - {None}:
                meshes[path].append((identity, row, model, preload_row))

    for flow_row, flow in flow_states:
        actions = flow.get('Actions')
        encoded = isinstance(actions, str)
        if encoded:
            try:
                actions = json.loads(actions)
            except (ValueError, TypeError):
                continue
        if not isinstance(actions, list):
            continue
        for index, action in enumerate(actions):
            if not isinstance(action, dict) or action.get('Name') != 'ShowTalk':
                continue
            params = action.get('Params')
            if not isinstance(params, dict):
                continue
            da = _package(params.get('SequenceDataAsset'))
            da_exports = assets.get(da)
            if not _properties(da_exports):
                continue
            emitted = set()
            for da_props in _properties(da_exports):
                generated = _package(da_props.get('GeneratedData'))
                generated_exports = assets.get(generated)
                for generated_props in _properties(generated_exports):
                    bindings = generated_props.get('BindingBP')
                    if not isinstance(bindings, list):
                        continue
                    for binding in bindings:
                        actor = _package(binding)
                        exports = assets.get(actor)
                        if not isinstance(exports, list):
                            continue
                        defaults, owners = set(), set()
                        for obj in exports:
                            if not isinstance(obj, dict) or not isinstance(obj.get('Properties'), dict):
                                continue
                            props = obj['Properties']
                            name = obj.get('Name')
                            talk = props.get('TalkID')
                            if isinstance(name, str) and name.lower().startswith('default__') and type(talk) is int and talk > 0:
                                defaults.add(talk)
                            if obj.get('Type') == 'SkeletalMeshComponent':
                                owners.update(meshes.get(_package(props.get('SkeletalMesh')), []))
                        if len(defaults) != 1 or len(owners) != 1:
                            continue
                        role_id, role_row, model, preload_row = next(iter(owners))
                        if role_counts[role_id] != 1 or preload_counts[model] != 1:
                            continue
                        default = next(iter(defaults))
                        candidates = [(default, 'default_talker')]
                        items = params.get('TalkItems')
                        if isinstance(items, list):
                            for item in items:
                                who = item.get('WhoId') if isinstance(item, dict) else None
                                if type(who) is not int or who <= 0:
                                    continue
                                text = display(f'Speaker_{who}_Name')
                                if isinstance(text, str) and text and names.get(text) == [role_id]:
                                    candidates.append((who, 'cast_and_corroborated_name'))
                        raw_path = f'$[{flow_row}].Actions{"<json>" if encoded else ""}[{index}].Params.SequenceDataAsset'
                        for who, resolution in candidates:
                            pair = (who, role_id)
                            if pair in emitted:
                                continue
                            emitted.add(pair)
                            yield {
                                'from': f'speaker:{who}', 'to': f'character:{role_id}',
                                'type': 'references_character', 'relation': 'exact_join',
                                'source': FLOW_PATH, 'raw_path': raw_path,
                                'basis': SEQUENCE_ACTOR_BASIS + f'{da} -> {generated} -> {actor}; model {model}, default talker {default}; {resolution}',
                                'crosswalk': {
                                    'method': 'exact_sequence_actor', 'resolution': resolution,
                                    'model_preload_id': model, 'model_preload_row': preload_row,
                                    'role_id': role_id, 'role_row': role_row,
                                    'sequence_asset_package': da, 'sequence_asset_sha256': _digest(da_exports),
                                    'generated_asset_package': generated, 'generated_asset_sha256': _digest(generated_exports),
                                    'actor_package': actor, 'actor_sha256': _digest(exports),
                                    'actor_exports': exports, 'default_talker_id': default,
                                },
                            }
