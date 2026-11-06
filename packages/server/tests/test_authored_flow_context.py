import json
from copy import deepcopy
from pathlib import Path

import pytest

from wuwa_story.ingestion.authored_flow_context import RECIPE, authored_flow_context


def fixture():
    quests = [(7, {'Key': '42_3', 'Data': {'Condition': {'Conditions': [
        {'Flow': {'FlowListName': 'Story', 'FlowId': 6, 'StateId': 19}}]}}})]
    flows = [(11, {'Id': 'Story_6', 'States': [19, 20, 21]})]
    states = [(12, {'Id': 19, 'StateKey': 'Story_6_19', 'Actions': '[]'}),
              (13, {'Id': 20, 'StateKey': 'Story_6_20', 'Actions': json.dumps([
                  {'Name': 'Branch', 'Actions': [{'Name': 'PlayMovie', 'Params': {'VideoName': 'M0357'}}]}])}),
              (14, {'Id': 21, 'StateKey': 'Story_6_21', 'Actions': json.dumps([
                  {'Name': 'ShowTalk', 'Params': {'TalkItems': [{'TidTalk': 'Caption'}]}}])})]
    return quests, flows, states


def test_exact_group_context_preserves_membership_anchor_and_uncertainty():
    link, = authored_flow_context(*fixture())
    assert (link['from'], link['to'], link['type']) == (
        'quest:42', 'flow_state:Story_6_20', 'references_scenario_flow_state')
    assert link['relation'] == 'inference' and link['confidence'] == 'possible'
    assert link['candidate_quest_count'] == 1 and link['runtime_order_confirmed'] is False
    proof = json.loads(link['basis'])
    assert proof['recipe'] == RECIPE and proof['basis'] == 'same_authored_flow_group'
    assert proof['flow_membership']['row'] == 11
    assert proof['flow_membership']['states'] == [19, 20, 21]
    assert proof['anchors'][0]['raw_path'] == '$[7].Data.Condition.Conditions[0].Flow'
    assert proof['media'][0]['raw_path'] == '$[13].Actions[0].Actions[0].Params.VideoName'


@pytest.mark.parametrize('mutation', ['missing_group', 'missing_member', 'wrong_id', 'anchor_not_member'])
def test_prefix_alone_or_invalid_anchor_is_insufficient(mutation):
    quests, flows, states = fixture()
    if mutation == 'missing_group':
        flows[0][1]['Id'] = 'Other_6'
    elif mutation == 'missing_member':
        flows[0][1]['States'].remove(20)
    elif mutation == 'wrong_id':
        states[1][1]['Id'] = 21
    else:
        flows[0][1]['States'].remove(19)
    assert list(authored_flow_context(quests, flows, states)) == []


def test_competing_quests_are_all_possible_and_keep_separate_anchor_proof():
    quests, flows, states = fixture()
    other = deepcopy(quests[0][1])
    other['Key'] = '43_8'
    quests.append((9, other))
    links = list(authored_flow_context(quests, flows, states))
    assert {link['from'] for link in links} == {'quest:42', 'quest:43'}
    assert all(link['candidate_quest_count'] == 2 for link in links)
    for link in links:
        assert all(anchor['from'].split(':')[1] == link['from'].split(':')[1]
                   for anchor in json.loads(link['basis'])['anchors'])


@pytest.mark.parametrize('relation', ['references_dungeon_flow_state', 'references_scenario_flow_state'])
def test_external_original_anchors_supported_but_recipe_outputs_never_reanchor(relation):
    _, flows, states = fixture()
    anchor = {'from': 'quest_node:42:3', 'to': 'flow_state:Story_6_19', 'type': relation,
              'source': 'original.json', 'raw_path': '$[5].Condition', 'basis': 'original proof'}
    link, = authored_flow_context([], flows, states, [anchor, anchor])
    assert json.loads(link['basis'])['anchors'] == [anchor]
    assert list(authored_flow_context([], flows, states, [link])) == []
    # Stored canonical evidence loses extra fields; the versioned basis still fences reuse.
    stored = {key: link[key] for key in (
        'from', 'to', 'type', 'source', 'raw_path', 'basis')}
    assert list(authored_flow_context([], flows, states, [stored])) == []


def test_sequence_wrapper_is_included_and_talk_only_caption_is_not():
    quests, flows, states = fixture()
    states[1][1]['Actions'] = json.dumps(
        [{'Name': 'PlaySequenceData', 'Params': {'Path': '/Game/Cinematic'}}])
    link, = authored_flow_context(quests, flows, states)
    assert json.loads(link['basis'])['media'][0]['kind'] == 'sequence'


def test_real_m0357_retained_release21_fixture():
    data = json.loads((Path(__file__).parent / 'fixtures/m0357_authored_flow_context.json').read_text(encoding='utf-8'))
    anchors = [{'from': row['owner'], 'to': row['target'], 'type': row['relation'],
                'source': row['file'], 'raw_path': row['raw_path'], 'basis': row['metadata']['source_basis']}
               for row in data['anchor_edges']]
    def rows(key):
        return [(row['row'], row['data']) for row in data[key]]
    requirements = rows('quest_requirement_sources')
    links = list(authored_flow_context(
        [(index, row) for index, row in requirements if row.get('Key', '').startswith('121000040_')],
        rows('flow_records'), rows('target_flow_states'), anchors,
        [(index, row) for index, row in requirements if 'QuestId' in row]))
    movie, = [link for link in links if link['to'].endswith('_6_20')]
    proof = json.loads(movie['basis'])
    assert movie['from'] == 'quest:121000040' and movie['candidate_quest_count'] == 1
    assert proof['media'][0]['reference'] == 'cutscene:M0357'
    assert proof['flow_membership']['flow_id'] == data['flow_id']
    assert proof['runtime_order_confirmed'] is False
    assert {anchor['source'] for anchor in proof['anchors']} == {
        'BinData/QuestNodeData/questnodedata.json', 'BinData/PlotHandBook/plothandbookconfig.json'}


@pytest.mark.parametrize('flows', [[], [(11, {'Id': 'Other_2', 'States': [3]})]])
def test_known_nested_calls_survive_missing_caller_or_target_group_and_unknown_branch(flows):
    quests = [(7, {'Key': '42_3', 'Data': {'Condition': {
        'FlowListName': 'Story', 'FlowId': 1, 'StateId': 1}}})]
    states = [(1, {'Id': 1, 'StateKey': 'Story_1_1', 'Actions': json.dumps([
        {'Name': 'Branch', 'Actions': [
            {'Name': 'PlayFlow', 'Params': {'FlowListName': 'Missing', 'FlowId': 9, 'StateId': 9}},
            {'Name': 'PlayFlow', 'Params': {'FlowListName': 'Other', 'FlowId': 2, 'StateId': 3}}]}])}),
        (2, {'Id': 3, 'StateKey': 'Other_2_3', 'Actions': json.dumps([
            {'Name': 'PlayMovie', 'Params': {'VideoName': 'M0357'}}])})]
    link, = authored_flow_context(quests, flows, states)
    proof = json.loads(link['basis'])
    assert (link['from'], link['to'], link['confidence']) == ('quest:42', 'flow_state:Other_2_3', 'possible')
    assert proof['recipe'] == RECIPE and proof['runtime_order_confirmed'] is False
    if not flows:
        assert link['context_basis'] == 'explicit_flow_call_chain'
        assert proof['call_chain'][0]['raw_path'] == '$[1].Actions[0].Actions[1].Params'
    else:
        assert proof['anchors'][0]['type'] == 'references_scenario_flow_state'
    assert list(authored_flow_context([], flows, states, [link])) == []
