import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('npc_interactions', Path(__file__).parents[1] / 'npc_research_interactions.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_flow_triple_keeps_multiple_speakers_without_identity_claim():
    subjects = [{'label': str(i), 'speaker_candidates': [{'speaker_key': f'speaker:{i}',
        'lines': [{'flow_list_name': 'flow', 'flow_id': 1, 'state_id': 2, 'node_id': i}]}]}
        for i in (10, 20)]
    entities = [{'Id': 1, 'EntityId': 30, 'ComponentsData': {'Options': [
        {'FlowListName': 'flow', 'FlowId': 1, 'StateId': 2},
        {'FlowListName': 'flow', 'FlowId': 1, 'StateId': 3}]}}]
    rows = list(module.link_interactions(entities, {'subjects': subjects}))
    assert len(rows) == 1
    assert len(rows[0]['interactions']) == 1
    match = rows[0]['interactions'][0]
    assert {p['speaker_key'] for p in match['participants']} == {'speaker:10', 'speaker:20'}
    assert match['establishes_personal_identity'] is False
    assert match['source_path'] == '$.Options[0]'
