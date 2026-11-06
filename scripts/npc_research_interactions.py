"""Match world interactions to dialogue evidence without asserting personal identity."""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def flow_references(value, path='$'):
    if isinstance(value, dict):
        if all(key in value for key in ('FlowListName', 'FlowId', 'StateId')):
            yield path, (value['FlowListName'], value['FlowId'], value['StateId'])
        for key, child in value.items():
            yield from flow_references(child, f'{path}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from flow_references(child, f'{path}[{index}]')


def link_interactions(entities, dialogue):
    states = defaultdict(list)
    for subject in dialogue['subjects']:
        for speaker in subject['speaker_candidates']:
            for line in speaker['lines']:
                triple = (line['flow_list_name'], line['flow_id'], line['state_id'])
                state_key = line.get('state_key') or f'{triple[0]}_{triple[1]}_{triple[2]}'
                states[state_key].append({'subject': subject['label'],
                    'speaker_key': speaker['speaker_key'], 'line_node_id': line['node_id']})
    for row_index, entity in enumerate(entities):
        matches = []
        for path, triple in flow_references(entity.get('ComponentsData', {})):
            state_key = f'{triple[0]}_{triple[1]}_{triple[2]}'
            if state_key in states:
                matches.append({'source_path': path, 'flow': list(triple),
                    'participants': states[state_key], 'basis': 'exact_flow_triple',
                    'establishes_personal_identity': False})
        if matches:
            yield {'source_row': row_index, 'config_id': entity.get('Id'),
                'entity_id': entity.get('EntityId'), 'map_id': entity.get('MapId'),
                'blueprint_type': entity.get('BlueprintType'), 'source_name': entity.get('Name'),
                'interactions': matches}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entities', required=True, type=Path)
    parser.add_argument('--dialogue', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    rows = list(link_interactions(json.loads(args.entities.read_text(encoding='utf-8-sig')),
                                  json.loads(args.dialogue.read_text(encoding='utf-8-sig'))))
    args.out.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps({'matched_world_instances': len(rows), 'out': str(args.out)}))
