import json
from copy import deepcopy

import pytest

from wuwa_story.ingestion.flow_call_context import RECIPE, SOURCE, expand_flow_anchors


def call(name='Other', flow=2, state=3):
    return {'Name': 'PlayFlow', 'Params': {'FlowListName': name, 'FlowId': flow, 'StateId': state}}


def row(index, key, actions):
    return index, {'Id': int(key.rsplit('_', 1)[1]), 'StateKey': key, 'Actions': json.dumps(actions)}


def seed(**changes):
    return {'from': 'quest_node:42:7', 'to': 'flow_state:Story_1_1',
            'type': 'references_flow_state', 'source': 'original.json',
            'raw_path': '$[5].Data.Condition.Flow', 'basis': 'explicit triple',
            'version': '3.7.0', 'source_commit': 'release21', **changes}


def test_nested_cross_group_calls_preserve_original_owner_caller_rows_and_source_version():
    rows = [row(100, 'Story_1_1', [{'Name': 'Branch', 'Actions': [call()]}]),
            row(250, 'Other_2_3', [{'Condition': {'Type': 'PlayFlow',
                 'Flow': {'FlowListName': 'Third', 'FlowId': 8, 'StateId': 9}}}]),
            row(300, 'Third_8_9', [])]
    anchor = seed()
    before = deepcopy((rows, anchor))
    first, second = expand_flow_anchors(rows, [anchor])
    assert [first['to'], second['to']] == ['flow_state:Other_2_3', 'flow_state:Third_8_9']
    assert second['from'] == 'quest_node:42:7'
    assert second['source'] == SOURCE
    assert second['raw_path'] == '$[250].Actions[0].Condition.Flow'
    assert second['version'] == '3.7.0' and second['source_commit'] == 'release21'
    proof = json.loads(second['basis'])
    assert proof['recipe'] == RECIPE and proof['anchor']['raw_path'] == anchor['raw_path']
    assert [step['source_key'] for step in proof['call_chain']] == ['Story_1_1', 'Other_2_3']
    assert [step['row'] for step in proof['call_chain']] == [100, 250]
    assert proof['call_chain'][0]['raw_path'] == '$[100].Actions[0].Actions[0].Params'
    assert second['confidence'] == 'possible' and second['runtime_order_confirmed'] is False
    assert (rows, anchor) == before


def test_unknown_and_malformed_branches_before_valid_call_do_not_stop_traversal():
    bad = [call('Missing'), call(flow=True), call(state='3'), {'Name': 'PlayFlow', 'Params': 'bad'},
           {'Type': 'PlayFlow', 'Flow': None}, call()]
    rows = [row(10, 'Story_1_1', [{'Branches': bad}]), row(20, 'Other_2_3', [])]
    links = list(expand_flow_anchors(rows, [seed()]))
    assert len(links) == 1 and links[0]['to'] == 'flow_state:Other_2_3'
    assert links[0]['raw_path'] == '$[10].Actions[0].Branches[5].Params'


def test_cycles_repeated_seeds_and_diamond_calls_are_bounded_and_deduplicated():
    rows = [row(10, 'Story_1_1', [call(), call(), call('Third', 8, 9)]),
            row(20, 'Other_2_3', [call('Story', 1, 1), call('Third', 8, 9)]),
            row(30, 'Third_8_9', [call()])]
    links = list(expand_flow_anchors(rows, [seed(), seed()]))
    assert len(links) == 2
    assert {link['to'] for link in links} == {'flow_state:Other_2_3', 'flow_state:Third_8_9'}


@pytest.mark.parametrize('basis', [
    {'recipe': 'authored-flow-context-v1'}, {'basis': 'same_authored_flow_group'}, {'recipe': RECIPE},
])
def test_inferred_recipe_records_are_never_new_seeds(basis):
    rows = [row(1, 'Story_1_1', [call()]), row(2, 'Other_2_3', [])]
    assert list(expand_flow_anchors(rows, [seed(basis=json.dumps(basis))])) == []


def test_authored_context_marker_is_fenced_and_numeric_neighbors_are_not_followed():
    rows = [row(1, 'Story_1_1', []), row(2, 'Story_1_2', [call()]), row(3, 'Other_2_3', [])]
    assert list(expand_flow_anchors(rows, [seed()])) == []
    rows[0] = row(1, 'Story_1_1', [call()])
    assert list(expand_flow_anchors(rows, [seed(context_basis='same_authored_flow_group')])) == []


def test_original_quests_remain_separate_and_output_can_seed_authored_group_helper():
    from wuwa_story.ingestion.authored_flow_context import authored_flow_context

    rows = [row(1, 'Story_1_1', [call()]), row(2, 'Other_2_3', []),
            row(3, 'Other_2_4', [{'Name': 'PlayMovie', 'Params': {'VideoName': 'M0357'}}])]
    expanded = list(expand_flow_anchors(rows, [seed(), seed(**{'from': 'quest:43'})]))
    assert {link['from'] for link in expanded} == {'quest_node:42:7', 'quest:43'}
    links = list(authored_flow_context([], [(4, {'Id': 'Other_2', 'States': [3, 4]})], rows, expanded))
    assert {link['from'] for link in links} == {'quest:42', 'quest:43'}
    assert all(link['candidate_quest_count'] == 2 for link in links)
