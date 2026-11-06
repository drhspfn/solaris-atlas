"""Possible quest context along explicit PlayFlow calls, never execution order."""

import json
from collections import deque

from wuwa_story.ingestion.dungeon_context import decoded, walk

SOURCE = 'BinData/flowState/flowstate.json'
RECIPE = 'flow-call-context-v1'
RELATION = 'references_scenario_flow_state'


def expand_flow_anchors(state_rows, anchors):
    """Traverse known callees once per original seed; unknown branches are local misses."""
    states = {row['StateKey']: (index, row) for index, row in state_rows
              if isinstance(row.get('StateKey'), str) and row['StateKey']}
    calls = {}
    for key, (index, row) in states.items():
        calls[key] = []
        for path, action in walk(row.get('Actions'), f'$[{index}].Actions'):
            field = ('Params' if action.get('Name') == 'PlayFlow' else
                     'Flow' if action.get('Type') == 'PlayFlow' else None)
            params = decoded(action.get(field)) if field else None
            if not isinstance(params, dict):
                continue
            name, flow, state = (params.get(k) for k in ('FlowListName', 'FlowId', 'StateId'))
            if not isinstance(name, str) or not name or type(flow) is not int or type(state) is not int:
                continue
            target = f'{name}_{flow}_{state}'
            if target not in states:
                continue
            calls[key].append({'source_key': key, 'target_key': target, 'source': SOURCE,
                               'row': index, 'raw_path': f'{path}.{field}'})

    seen_seeds = set()
    for anchor in anchors:
        if anchor.get('type') not in ('references_flow_state', 'references_dungeon_flow_state', RELATION):
            continue
        evidence = decoded(anchor.get('basis'))
        if (anchor.get('context_basis') == 'same_authored_flow_group' or
                isinstance(evidence, dict) and (
                    evidence.get('recipe') in ('authored-flow-context-v1', RECIPE) or
                    evidence.get('basis') == 'same_authored_flow_group')):
            continue
        owner = anchor.get('from', '').split(':')
        start = anchor.get('to', '').removeprefix('flow_state:')
        if (len(owner) not in (2, 3) or owner[0] not in ('quest', 'quest_node') or
                not owner[1].isdecimal() or start not in states):
            continue
        seed = {key: anchor.get(key) for key in ('from', 'to', 'type', 'source', 'raw_path', 'basis')}
        identity = json.dumps(seed, sort_keys=True)
        if identity in seen_seeds:
            continue
        seen_seeds.add(identity)
        visited = {start}
        pending = deque([(start, [])])
        while pending:
            caller, chain = pending.popleft()
            for call in calls[caller]:
                target = call['target_key']
                if target in visited:
                    continue
                visited.add(target)
                call_chain = [*chain, call]
                pending.append((target, call_chain))
                yield {'from': anchor['from'], 'to': f'flow_state:{target}', 'type': RELATION,
                       'relation': 'inference', 'confidence': 'possible',
                       'context_basis': 'explicit_flow_call_chain', 'runtime_order_confirmed': False,
                       'source': SOURCE, 'raw_path': call['raw_path'],
                       **{key: anchor[key] for key in ('version', 'source_repository', 'source_commit') if key in anchor},
                       'basis': json.dumps({'recipe': RECIPE, 'basis': 'explicit_flow_call_chain',
                           'confidence': 'possible', 'runtime_order_confirmed': False,
                           'meaning': 'Possible context from explicit calls; execution and quest ownership are unconfirmed',
                           'anchor': seed, 'call_chain': call_chain}, ensure_ascii=False, sort_keys=True)}
