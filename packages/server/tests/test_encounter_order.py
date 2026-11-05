"""Encounter projection: local authored paths are not a global playthrough."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData

from wuwa_story.story import tree_order
from wuwa_story.story.runtime_blocks import (
    cinematic_boundaries,
    dungeon_requirement,
    project_blocks,
)


def nodes(records, paths):
    return [{'id': identity, 'path': path, 'type': records[identity]['Type'],
             'parent_id': records[identity].get('ParentNodeId', 0),
             'record_sha256': hashlib.sha256(json.dumps(records[identity], sort_keys=True,
                 separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}
            for identity, path in paths]


def flow(identity):
    return {'FlowListName': 'Story', 'FlowId': identity, 'StateId': 1}


def quest():
    records = {231: {'Id': 231, 'Type': 'ChildQuest', 'TidTip': 'Fight',
        'Condition': {'Type': 'FinishDungeon', 'Condition': {
            'Type': 'DungeonId', 'Count': 1, 'DungeonId': 1555, 'IsExitDungeon': True}}}}
    return nodes(records, [(231, [9, 1])]), records


def level():
    # Source-derived topology: the ID order deliberately differs from child order.
    records = {
        32: {'Id': 32, 'Type': 'Sequence'},
        4: {'Id': 4, 'Type': 'ChildQuest', 'ParentNodeId': 32,
            'Condition': {'Type': 'WaitBattleCondition', 'Count': 0,
                          'Option': [{'Type': 'BattleTag', 'EntityId': 114750013, 'TagConfigId': 5}]},
            'EnterActions': [{'Name': 'PlayFlow', 'Async': True, 'Params': flow(27)}]},
        66: {'Id': 66, 'Type': 'ChildQuest', 'ParentNodeId': 32,
             'Condition': {'Type': 'PlayFlow', 'Flow': flow(28)}},
        54: {'Id': 54, 'Type': 'ParallelSelect', 'ParentNodeId': 32, 'Count': 1},
        56: {'Id': 56, 'Type': 'ChildQuest', 'ParentNodeId': 54,
             'Condition': {'Type': 'WaitBattleCondition'},
             'EnterActions': [{'Name': 'PlayFlow', 'Async': True, 'Params': flow(29)}]},
        58: {'Id': 58, 'Type': 'ChildQuest', 'ParentNodeId': 54,
             'Condition': {'Type': 'WaitBattleCondition'},
             'EnterActions': [{'Name': 'PlayFlow', 'Async': True, 'Params': flow(29)}]},
    }
    return nodes(records, [(58, [2, 1]), (66, [1]), (32, []), (56, [2, 0]),
                           (54, [2]), (4, [0])]), records


def memberships():
    return {114000004: {'dungeon_id': 1555, 'file': 'BinData/LevelPlayData/levelplaydata.json',
                       'row': 4338, 'release_id': 21}}


def flatten(entry):
    return [entry, *(child for branch in entry['children'] for child in flatten(branch))]


def test_encounter_keeps_sequence_parallel_occurrences_async_and_cinematic_boundaries():
    movie = {'StateKey': 'Story_28_1', 'Actions': json.dumps([{'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': '/Game/Aki/Sequence/LevelA_Seq/Main/03_1/M0334/DA_M0334.DA_M0334',
        'SequenceNames': ['M0334_1Z']}}]),
        '_source': {'record_id': 64652952, 'row': 11844, 'release_id': 21}}
    qnodes, qrecords = quest()
    blocks = project_blocks(114000031, qnodes, qrecords, {114000004: level()},
        dungeon_memberships=memberships(), flow_records={'Story_28_1': movie},
        state_ids={'Story_28_1': 999}, release_id=21)
    encounter = blocks[0]['encounter']
    assert encounter['release_id'] == 21
    assert encounter['trees_order'] == 'unknown'
    root = encounter['trees'][0]['root']
    assert root['children_order'] == 'sequence'
    assert [node['owner'] for node in root['children']] == [
        'level_play_node:114000004:4', 'level_play_node:114000004:66', 'level_play_node:114000004:54']
    fight, cinema, alternatives = root['children']
    assert fight['condition']['Option'][0]['TagConfigId'] == 5
    assert fight['flow_states'][0]['async'] is True
    assert fight['flow_states'][0]['timing'] == 'during'
    assert fight['flow_states'][0]['source']['raw_path'] == '$.Data.EnterActions[0].Params'
    cinematic_flow = cinema['flow_states'][0]
    assert cinematic_flow['timing'] == 'blocking'
    assert cinematic_flow['phase'] == 'Condition'
    assert cinematic_flow['state_node_id'] == 999
    media, = cinematic_flow['cinematics']
    assert media['renderable'] is False
    assert media['kind'] == 'sequence'
    assert media['sequence_names'] == ['M0334_1Z']
    assert media['source']['record_id'] == 64652952
    assert media['source']['release_id'] == 21
    assert media['boundary_semantics'] == 'authored_action_extent'
    assert media['start'] == {'action_index': 0, 'anchor': 'before_action'}
    assert media['end'] == {'action_index': 0, 'anchor': 'after_action'}
    assert alternatives['children_order'] == 'parallel'
    assert alternatives['count'] == 1
    refs = [node['flow_states'][0] for node in alternatives['children']]
    assert [ref['state'] for ref in refs] == ['Story_29_1', 'Story_29_1']
    assert len({ref['id'] for ref in refs}) == 2
    assert all('position' not in node for node in flatten(root))
    # Membership does not turn local branch paths into flat transcript ranks.
    assert tree_order.flow_positions(qnodes, qrecords, {114000004: level()},
                                     {'Story_27_1', 'Story_28_1', 'Story_29_1'}) == []


def test_multiple_dungeon_trees_have_no_sequence_and_wrong_membership_is_excluded():
    qnodes, qrecords = quest()
    proofs = {**memberships(), 114750000: {'dungeon_id': 1555}, 888: {'dungeon_id': 999},
              777: {'dungeon_id': 1555}}
    blocks = project_blocks(114000031, qnodes, qrecords,
        {888: level(), 114750000: level(), 114000004: level()}, dungeon_memberships=proofs)
    encounter = blocks[0]['encounter']
    assert encounter['trees_order'] == 'unknown'
    assert {tree['level_play_id'] for tree in encounter['trees']} == {114000004, 114750000}
    assert all('position' not in tree for tree in encounter['trees'])
    assert encounter['status'] == 'partial'
    assert encounter['unresolved_level_plays'] == [{'level_play_id': 777,
        'membership': {'dungeon_id': 1555}, 'reason': 'matching_authored_tree_unavailable'}]
    empty = project_blocks(114000031, qnodes, qrecords, {114000004: level()})
    assert empty[0]['encounter']['trees'] == []


def test_selector_conditions_and_decorators_are_preserved_without_evaluating_them():
    records = {
        18: {'Id': 18, 'Type': 'ConditionSelector', 'Slots': [
            {'Node': {'Id': 19}, 'Condition': {'Conditions': [{'Type': 'CheckPlayerGender', 'Gender': '男'}]}}]},
        19: {'Id': 19, 'Type': 'AlwaysFalse', 'ParentNodeId': 18},
        23: {'Id': 23, 'Type': 'Action', 'ParentNodeId': 19,
             'Actions': [{'Name': 'PlayMovie', 'Params': {'VideoName': 'ExplicitCg'}}]},
    }
    qnodes, qrecords = quest()
    tree = nodes(records, [(18, []), (19, [0]), (23, [0, 0])])
    root = project_blocks(114000031, qnodes, qrecords, {114000004: (tree, records)},
        dungeon_memberships=memberships())[0]['encounter']['trees'][0]['root']
    assert root['children_order'] == 'conditional'
    branch, = root['children']
    assert branch['branch_condition'] == records[18]['Slots'][0]['Condition']
    assert branch['children_order'] == 'decorator'
    assert branch['node_type'] == 'AlwaysFalse'
    media, = branch['children'][0]['media']
    assert media['reference'] == 'cutscene:ExplicitCg'
    assert media['source']['raw_path'] == '$.Data.Actions[0].Params.VideoName'


@pytest.mark.parametrize('condition', [None, 'opaque', {'Type': 'FinishDungeon'},
    {'Type': 'FinishDungeon', 'Condition': {'Type': 'DungeonId', 'Count': 2, 'DungeonId': 1555}},
    {'Type': 'FinishDungeon', 'Condition': {'Type': 'DungeonId', 'Count': 1, 'DungeonId': '1555'}}])
def test_no_dungeon_inference_from_ambiguous_conditions(condition):
    assert dungeon_requirement({'Condition': condition}) is None


def test_bad_actions_and_unproven_sequence_names_do_not_create_cinematics():
    assert cinematic_boundaries({'Actions': 'opaque'}) == []
    assert cinematic_boundaries({'Actions': [{'Name': 'ShowTalk', 'Params': {
        'SequenceNames': ['M0334_1Z']}}]}) == []


def test_root_comes_from_client_metadata_even_if_nonroot_raw_parent_is_missing():
    tree, records = level()
    del records[4]['ParentNodeId']
    records[32]['ParentNodeId'] = 12345
    # Dict and input order both put a non-root first; only client paths are authoritative.
    records = {4: records[4], **records}
    qnodes, qrecords = quest()
    root = project_blocks(114000031, qnodes, qrecords, {114000004: (tree, records)},
                          dungeon_memberships=memberships())[0]['encounter']['trees'][0]['root']
    assert root['owner'] == 'level_play_node:114000004:32'
    assert root['children'][0]['owner'] == 'level_play_node:114000004:4'


@pytest.mark.parametrize('defect', ['no_root', 'two_roots', 'orphan', 'bad_path', 'missing_metadata', 'empty'])
def test_bad_client_topology_becomes_diagnostic_not_a_server_error(defect):
    tree, records = level()
    root = next(node for node in tree if node['id'] == 32)
    if defect == 'no_root':
        root['parent_id'] = 12345
    elif defect == 'two_roots':
        tree.append({**root, 'id': 888})
    elif defect == 'orphan':
        tree[0]['parent_id'] = 12345
    elif defect == 'bad_path':
        tree[0]['path'] = [9, 9]
    elif defect == 'missing_metadata':
        del tree[0]['id']
    else:
        tree = []
    qnodes, qrecords = quest()
    encounter = project_blocks(114000031, qnodes, qrecords, {114000004: (tree, records)},
        dungeon_memberships=memberships())[0]['encounter']
    assert encounter['status'] == 'unresolved'
    assert encounter['trees'] == []
    diagnostic, = encounter['unresolved_level_plays']
    assert diagnostic['reason'] == 'invalid_authored_tree'
    assert diagnostic['diagnostic']


def test_actions_and_saveconfig_are_projected_with_separate_lifecycle_and_exact_paths():
    records = {1: {'Id': 1, 'Type': 'Action',
        'Actions': [{'Name': 'PlayFlow', 'Async': False, 'Params': flow(40)},
                    {'Name': 'SetAudioState', 'Params': {'AudioConfig': {}}}],
        'SaveConfig': {'EnterActions': json.dumps([
            {'Name': 'PlayFlow', 'Async': True, 'Params': flow(41)},
            {'Name': 'PlayMovie', 'Params': {'VideoName': 'SaveOnlyMovie'}},
            {'Name': 'AwakeEntity', 'Params': {'EntityIds': [114750018]}}]),
            'DisableRollbackWhileReconnect': True}}}
    qnodes, qrecords = quest()
    root = project_blocks(114000031, qnodes, qrecords,
        {114000004: (nodes(records, [(1, [])]), records)},
        dungeon_memberships=memberships())[0]['encounter']['trees'][0]['root']
    normal, saved = root['flow_states']
    assert normal['phase'] == 'Actions'
    assert normal['timing'] == 'blocking'
    assert normal['source']['raw_path'] == '$.Data.Actions[0].Params'
    assert saved['phase'] == 'SaveConfig.EnterActions'
    assert saved['execution_context'] == 'save_config'
    assert saved['async'] is True and saved['timing'] == 'unknown'
    assert saved['source']['raw_path'] == '$.Data.SaveConfig.EnterActions[0].Params'
    movie, = root['media']
    assert movie['phase'] == 'SaveConfig.EnterActions'
    assert movie['execution_context'] == 'save_config'
    assert movie['source']['raw_path'] == '$.Data.SaveConfig.EnterActions[1].Params.VideoName'
    normal_section, saved_section = root['action_sections']
    assert normal_section['unprojected_actions'] == [{'index': 1, 'name': 'SetAudioState'}]
    assert saved_section['runtime_order'] == 'unknown'
    assert saved_section['unprojected_actions'] == [{'index': 2, 'name': 'AwakeEntity'}]


def result(columns, rows):
    return IteratorResult(SimpleResultMetaData(columns), iter(rows))


@pytest.mark.asyncio
@pytest.mark.parametrize(('matching', 'known_state'), [(True, True), (True, False), (False, False)])
@pytest.mark.parametrize('release_id', [1, 20, 21])
async def test_loader_pins_release_and_download_and_does_not_rank_dungeon_states(monkeypatch, matching, known_state, release_id):
    qnodes, qrecords = quest()
    lnodes, lrecords = level()
    quest_tree = SimpleNamespace(nodes=qnodes, asset_version='3.7.1', download_id='client')
    level_tree = SimpleNamespace(nodes=lnodes, tree_id=114000004)
    snapshot = {key: dict(value) for key, value in lrecords.items()}
    if not matching:
        snapshot[66] = {**snapshot[66], 'Condition': {'Type': 'PlayFlow', 'Flow': flow(99)}}
    session = SimpleNamespace(scalars=AsyncMock(side_effect=[
        [quest_tree], [{'Key': f'114000031_{key}', 'Data': value} for key, value in qrecords.items()],
        [level_tree], [{'Key': f'114000004_{key}', 'Data': value} for key, value in snapshot.items()],
        *([[{'LevelPlayId': 114000004, 'Data': {}}]] if matching else []),
    ]), execute=AsyncMock(side_effect=[
        result(['row_index', 'data', 'id'], [(4338, {'LevelPlayId': 114000004,
            'Data': {'Id': 114000004, 'Type': 'Dungeon', 'LevelId': 1555}}, 64201280)]),
        result(['state_key', 'node_id'], [('Story_27_1', 998), ('Story_28_1', 999),
                                         ('Story_29_1', 1000)] if known_state else []),
        *([result(['data', 'id', 'row_index'], [({'StateKey': 'Story_28_1',
            'Actions': [{'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': '/Game/M0334'}}]},
            64652952, 11844)])] if matching else []),
        result(['node_id', 'quest_state_node_id', 'action_index'], []),
    ]))
    monkeypatch.setattr(tree_order, 'source_parents', AsyncMock(return_value=[]))
    monkeypatch.setattr(tree_order, 'media_links', AsyncMock(return_value={}))
    # No new compiler crosswalk or dungeon edge exists in this session. The
    # retained source fields alone reconstruct membership, including old imports
    # with no materialized state node or an unrelated last-revision pointer.
    order = await tree_order.authored_order(session, 114000031, release_id)
    assert order['positions'] == {}
    assert order['state_ids'] == ([998, 999, 1000] if known_state else [])
    assert order['mapped_flow_states'] == []
    trees = order['blocks'][0]['encounter']['trees']
    assert len(trees) == int(matching)
    encounter = order['blocks'][0]['encounter']
    assert encounter['status'] == ('verified' if matching else 'unresolved')
    assert encounter['client'] == {'asset_version': '3.7.1', 'download_id': 'client'}
    if matching:
        cinema = next(node for node in flatten(trees[0]['root']) if node['owner'].endswith(':66'))
        assert cinema['flow_states'][0]['state_node_id'] == (999 if known_state else None)
        assert cinema['flow_states'][0]['cinematics'][0]['source']['release_id'] == release_id
        assert trees[0]['membership']['release_id'] == release_id
        # Both parallel branches still contain Flow29; fetch its lines once.
        occurrences = [flow for node in flatten(trees[0]['root']) for flow in node['flow_states']
                       if flow['state'] == 'Story_29_1']
        assert len(occurrences) == 2
        assert occurrences[0]['id'] != occurrences[1]['id']
    membership_sql = session.execute.call_args_list[0].args[0].compile()
    assert 'source_file.release_id' in str(membership_sql)
    assert release_id in membership_sql.params.values()
    assert encounter['release_id'] == release_id
    client_sql = session.scalars.call_args_list[2].args[0].compile()
    assert 'client' in client_sql.params.values()
    assert '3.7.1' in client_sql.params.values()


@pytest.mark.asyncio
async def test_legacy_import_without_client_tree_does_not_guess_sibling_order():
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]))
    assert await tree_order.authored_order(session, 114000031, 1) is None
    session.scalars.assert_awaited_once()


# Full retained evidence stays private; the deterministic tests above run in CI.
SOURCE = Path(__file__).resolve().parents[3] / 'scratch/boss1555-source-tree.json'
FLOWS = SOURCE.with_name('boss1555-flow-states.json')


@pytest.mark.skipif(not SOURCE.exists() or not FLOWS.exists(), reason='Retained boss1555 source exports')
def test_real_boss1555_full_composition_and_m0334():
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    records = {row['data']['Data']['Id']: row['data']['Data'] for row in source['level_nodes']}
    # Audited client3.7.1 child paths, not IDs or raw row order.
    paths = [(1, []), (9, [0]), (63, [0, 0]), (67, [0, 0, 0]),
        (31, [0, 0, 0, 0]), (2, [0, 0, 0, 1]), (32, [0, 0, 0, 2]),
        (18, [0, 0, 0, 2, 0]), (19, [0, 0, 0, 2, 0, 0]), (23, [0, 0, 0, 2, 0, 0, 0]),
        (21, [0, 0, 0, 2, 0, 1]), (24, [0, 0, 0, 2, 0, 1, 0]),
        (4, [0, 0, 0, 2, 1]), (66, [0, 0, 0, 2, 2]), (48, [0, 0, 1]),
        (49, [0, 0, 1, 0]), (50, [0, 0, 1, 0, 0]), (51, [0, 0, 1, 0, 0, 0]),
        (52, [0, 0, 1, 0, 1]), (53, [0, 0, 1, 0, 1, 0]), (54, [0, 0, 1, 1]),
        (55, [0, 0, 1, 1, 0]), (56, [0, 0, 1, 1, 0, 0]),
        (57, [0, 0, 1, 1, 1]), (58, [0, 0, 1, 1, 1, 0]),
        (59, [0, 0, 1, 1, 2]), (60, [0, 0, 1, 1, 2, 0]),
        (61, [0, 0, 1, 1, 3]), (62, [0, 0, 1, 1, 3, 0]),
        (109, [0, 0, 1, 1, 4]), (110, [0, 0, 1, 1, 4, 0]),
        (68, [0, 0, 2]), (69, [0, 0, 2, 0]), (74, [0, 0, 2, 1]), (73, [0, 0, 2, 2]),
        (75, [0, 1]), (3, [1])]
    assert set(records) == {identity for identity, _ in paths}
    qnodes, _ = quest()
    actual_quest = next(row['data']['Data'] for row in source['quest_nodes']
                        if row['data']['Key'] == '114000031_231')
    flow_records = {row['data']['StateKey']: row['data']
                    for row in json.loads(FLOWS.read_text(encoding='utf-8'))}
    block, = project_blocks(114000031, qnodes, {231: actual_quest},
        {114000004: (nodes(records, paths), records)}, dungeon_memberships=memberships(),
        flow_records=flow_records, release_id=21)
    root = block['encounter']['trees'][0]['root']
    all_nodes = {int(node['owner'].split(':')[-1]): node for node in flatten(root)}
    assert len(all_nodes) == 37
    assert [child['owner'] for child in all_nodes[63]['children']] == [
        f'level_play_node:114000004:{identity}' for identity in (67, 48, 68)]
    assert [int(child['owner'].split(':')[-1]) for child in all_nodes[68]['children']] == [69, 74, 73]
    assert all_nodes[4]['flow_states'][0]['timing'] == 'during'
    assert all_nodes[66]['flow_states'][0]['cinematics'][0]['sequence_names'] == ['M0334_1Z']
    refs = [all_nodes[key]['flow_states'][0] for key in (56, 58)]
    assert refs[0]['state'] == refs[1]['state']
    assert refs[0]['id'] != refs[1]['id']
    assert all_nodes[74]['condition']['ExistTargets'] == [114750015]
    assert all_nodes[54]['count'] == 1
    assert all_nodes[73]['completions'][0]['is_success'] is True
    assert all_nodes[73]['completions'][0]['source']['raw_path'] == '$.Data.FinishActions[3].Params.IsSuccess'
    save_section, = all_nodes[9]['action_sections']
    assert save_section['phase'] == 'SaveConfig.EnterActions'
    assert save_section['runtime_order'] == 'unknown'
    assert len(save_section['unprojected_actions']) == len(records[9]['SaveConfig']['EnterActions'])
    for identity in (23, 24, 51, 53):
        section, = all_nodes[identity]['action_sections']
        assert section['phase'] == 'Actions'
        assert len(section['unprojected_actions']) == len(records[identity]['Actions'])
