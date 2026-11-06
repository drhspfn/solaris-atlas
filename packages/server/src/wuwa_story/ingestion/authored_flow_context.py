"""Possible cinematic context from exact authored Flow.States membership."""

import json
from collections import defaultdict

from wuwa_story.ingestion.dungeon_context import QUEST_NODES, decoded, walk
from wuwa_story.ingestion.flow_call_context import expand_flow_anchors
from wuwa_story.ingestion.runtime_media import runtime_media

FLOWS = 'BinData/flow/flow.json'
STATES = 'BinData/flowState/flowstate.json'
PLOT = 'BinData/PlotHandBook/plothandbookconfig.json'
RELATION = 'references_scenario_flow_state'
BASIS = 'same_authored_flow_group'
RECIPE = 'authored-flow-context-v1'


def authored_flow_context(quest_rows, flow_rows, state_rows, anchors=(), plot_rows=()):
    """One hop from original anchors only; competing quests remain possible candidates."""
    state_rows = list(state_rows)
    flows = {row['Id']: (index, row) for index, row in flow_rows
             if isinstance(row.get('Id'), str) and isinstance(row.get('States'), list)}
    members = {}
    for index, row in state_rows:
        key, identity = row.get('StateKey'), row.get('Id')
        if not isinstance(key, str) or type(identity) is not int:
            continue
        group, _, suffix = key.rpartition('_')
        flow = flows.get(group)
        if flow and suffix == str(identity) and identity in flow[1]['States']:
            members[key] = (group, index, row)

    original = list(anchors)
    for index, row in quest_rows:
        parts = row.get('Key', '').split('_')
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            continue
        for path, value in walk(row.get('Data'), f'$[{index}].Data'):
            name, flow, state = (value.get(k) for k in ('FlowListName', 'FlowId', 'StateId'))
            if isinstance(name, str) and type(flow) is int and type(state) is int:
                original.append({'from': f'quest_node:{parts[0]}:{parts[1]}',
                    'to': f'flow_state:{name}_{flow}_{state}', 'type': 'references_flow_state',
                    'source': QUEST_NODES, 'raw_path': path, 'basis': 'explicit_flow_triple'})
    for index, row in plot_rows:
        quest = row.get('QuestId')
        if type(quest) is not int or quest <= 0:
            continue
        for path, value in walk(row.get('Data'), f'$[{index}].Data'):
            name, flow, state = (value.get(k) for k in ('FlowListName', 'FlowId', 'StateId'))
            if isinstance(name, str) and type(flow) is int and type(state) is int:
                original.append({'from': f'quest:{quest}', 'to': f'flow_state:{name}_{flow}_{state}',
                    'type': 'references_flow_state', 'source': PLOT, 'raw_path': path,
                    'basis': 'explicit_plot_step_flow'})

    seeds = []
    for anchor in original:
        if anchor.get('type') not in ('references_flow_state', 'references_dungeon_flow_state', RELATION):
            continue
        evidence = decoded(anchor.get('basis'))
        if (anchor.get('context_basis') == BASIS or isinstance(evidence, dict)
                and (evidence.get('basis') == BASIS or evidence.get('recipe') == RECIPE)):
            continue
        seeds.append(anchor)
    reached = list(expand_flow_anchors(state_rows, seeds))
    original = seeds + reached
    states = {row['StateKey']: (index, row) for index, row in state_rows
              if isinstance(row.get('StateKey'), str)}
    call_candidates = defaultdict(set)
    ungrouped_calls = defaultdict(list)
    for anchor in reached:
        call_candidates[anchor['to']].add(anchor['from'].split(':')[1])
        if anchor['to'].removeprefix('flow_state:') not in members:
            ungrouped_calls[(anchor['from'].split(':')[1], anchor['to'])].append(anchor)
    for (quest, target), calls in sorted(ungrouped_calls.items()):
        key = target.removeprefix('flow_state:')
        index, row = states[key]
        media = list(runtime_media(row.get('Actions'), f'$[{index}].Actions'))
        if media:
            calls = sorted(calls, key=lambda call: call['basis'])
            proof = decoded(calls[0]['basis'])
            proof.update(recipe=RECIPE, candidate_quest_count=len(call_candidates[target]), media=media,
                         anchor_proofs=[decoded(call['basis']) for call in calls])
            yield {**calls[0], 'from': f'quest:{quest}', 'source': STATES, 'raw_path': f'$[{index}].Actions',
                   'candidate_quest_count': proof['candidate_quest_count'],
                   'basis': json.dumps(proof, ensure_ascii=False, sort_keys=True)}
    groups = defaultdict(lambda: defaultdict(list))
    for anchor in original:
        owner = anchor.get('from', '').split(':')
        member = members.get(anchor.get('to', '').removeprefix('flow_state:'))
        if (not member or len(owner) not in (2, 3) or owner[0] not in ('quest', 'quest_node')
                or not owner[1].isdecimal()):
            continue
        proof = {key: anchor[key] for key in ('from', 'to', 'type', 'source', 'raw_path', 'basis')}
        if proof not in groups[member[0]][owner[1]]:
            groups[member[0]][owner[1]].append(proof)

    for key, (group, index, row) in sorted(members.items()):
        candidates = groups.get(group, {})
        media = list(runtime_media(row.get('Actions'), f'$[{index}].Actions'))
        if not candidates or not media:
            continue
        flow_index, flow = flows[group]
        for quest, proofs in sorted(candidates.items()):
            proofs = sorted(proofs, key=lambda proof: json.dumps(proof, ensure_ascii=False, sort_keys=True))
            yield {'from': f'quest:{quest}', 'to': f'flow_state:{key}', 'type': RELATION,
                'relation': 'inference', 'confidence': 'possible', 'context_basis': BASIS,
                'candidate_quest_count': len(candidates), 'runtime_order_confirmed': False,
                'source': STATES, 'raw_path': f'$[{index}].Actions',
                'basis': json.dumps({'basis': BASIS, 'recipe': RECIPE, 'confidence': 'possible',
                    'candidate_quest_count': len(candidates), 'runtime_order_confirmed': False,
                    'meaning': 'Possible cinematic context; quest ownership and runtime order are unconfirmed',
                    'flow_membership': {'file': FLOWS, 'row': flow_index,
                        'raw_path': f'$[{flow_index}].States', 'flow_id': group,
                        'state_id': row['Id'], 'states': flow['States']},
                    'anchors': proofs, 'media': media}, ensure_ascii=False, sort_keys=True)}
