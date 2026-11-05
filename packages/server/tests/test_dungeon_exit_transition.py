"""The real1557 exit reuses a map, not another occurrence of its boss fight."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData

from wuwa_story.story import runtime_service, tree_order
from wuwa_story.story.runtime_blocks import project_blocks

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/dungeon1557_exit.json').read_text(encoding='utf8'))


def quest():
    raw = copy.deepcopy(FIXTURE['quest_node']['Data'])
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return [{'id': 94, 'type': 'ChildQuest', 'parent_id': 0, 'path': [9, 3], 'record_sha256': digest}], {94: raw}


def result(keys, rows=()):
    return IteratorResult(SimpleResultMetaData(keys), iter(rows))


@pytest.mark.asyncio
@pytest.mark.parametrize('related', [False, True])
async def test_actual1557_exit_keeps_instance_map_and_source_without_battle(monkeypatch, related):
    nodes, records = quest()
    if related:
        nodes = [*nodes, {**nodes[0], 'id': 95}]
    session = SimpleNamespace(scalars=AsyncMock(side_effect=[
        [SimpleNamespace(nodes=nodes, asset_version='3.7.1', download_id='client')],
        [{'Key': '114000031_94', 'Data': records[94]}],
    ]), execute=AsyncMock(side_effect=[
        result(['row_index', 'data', 'id']),
        result(['row_index', 'data', 'id'], [(10, FIXTURE['instance'], FIXTURE['instance_source_record_id'])]),
        result(['state_key', 'node_id']),
        result(['id', 'row_index', 'key'], [(FIXTURE['quest_source_record_id'], 20, '114000031_94')]) if related else
        result(['node_id', 'quest_state_node_id', 'action_index']),
    ]))
    monkeypatch.setattr(tree_order, 'source_parents', AsyncMock(return_value=[]))
    monkeypatch.setattr(tree_order, 'media_links', AsyncMock(return_value={}))
    order = await tree_order.authored_order(session, 114000031, 21)
    block, = order['blocks']
    transition = block['transition']
    assert transition['id'] == f"{block['id']}/dungeon_exit:1557"
    assert transition['kind'] == 'dungeon_exit'
    assert (transition['dungeon_id'], transition['map_config_id']) == (1557, 1555)
    assert transition['is_exit_dungeon'] is True
    assert transition['count'] == 1 and transition['status'] == 'verified'
    assert transition['source']['key'] == '114000031_94'
    assert transition['source']['raw_path'] == '$.Data.Condition.Condition'
    assert transition['instance_source']['record_id'] == 64689830
    assert transition['instance_source']['release_id'] == 21
    assert transition['instance_source']['raw_path'] == '$[10].MapConfigId'
    assert 'encounter' not in block
    assert order['state_ids'] == [] and order['mapped_flow_states'] == []
    if related:
        assert block['position'] is None and block['ordering'] == 'related'
        assert transition['source']['record_id'] == FIXTURE['quest_source_record_id']
    # The only membership lookup still uses the instance1557. No map1555
    # LevelPlay lookup, client tree loading, or new dialogue ranking is added.
    sql = session.execute.await_args_list[1].args[0].compile()
    assert 'source_file.release_id' in str(sql)
    assert 21 in sql.params.values()
    assert 'BinData/instance_dungeon/instancedungeon.json' in sql.params.values()
    assert session.scalars.await_count == 2
    monkeypatch.setattr(runtime_service.repository, 'named_localization_keys', AsyncMock(return_value={}))
    monkeypatch.setattr(runtime_service, 'prefetch_localizations', AsyncMock())
    presented, = await runtime_service.present_blocks(session, order, 'en', 21)
    assert presented['transition'] == transition
    assert 'encounter' not in presented


@pytest.mark.parametrize('flag', [False, None, 'true', 1])
def test_only_explicit_boolean_exit_can_replace_an_encounter(flag):
    nodes, records = quest()
    records[94]['Condition']['Condition']['IsExitDungeon'] = flag
    block, = project_blocks(114000031, nodes, records, {},
        dungeon_instances={1557: {'map_config_id': 1555, 'source': {}}})
    assert 'transition' not in block
    assert block['encounter']['trees'] == []


@pytest.mark.parametrize('mapping', [{}, {1557: {'map_config_id': 1557}}, {1557: {'map_config_id': True}},
                                    {1557: {'map_config_id': 0}}, {1557: {'map_config_id': '1555'}}])
def test_unknown_or_invalid_instance_map_does_not_infer_map_battle(mapping):
    nodes, records = quest()
    block, = project_blocks(114000031, nodes, records, {}, dungeon_instances=mapping)
    assert 'transition' not in block
    assert block['encounter']['status'] == 'unresolved'


def test_same_map_retains_original_battle_and_separate1557_exit_occurrence():
    nodes, records = quest()
    records[231] = {'Type': 'ChildQuest', 'Id': 231, 'Condition': {'Type': 'FinishDungeon',
        'Condition': {'Type': 'DungeonId', 'Count': 1, 'DungeonId': 1555, 'IsExitDungeon': True}}}
    nodes.insert(0, {'id': 231, 'type': 'ChildQuest', 'parent_id': 0, 'path': [9, 1]})
    tree = ([{'id': 1, 'type': 'ChildQuest', 'parent_id': 0, 'path': []}],
            {1: {'Type': 'ChildQuest', 'Condition': {'Type': 'WaitBattleCondition'}}})
    battle, exit_block = project_blocks(114000031, nodes, records, {114000004: tree},
        dungeon_memberships={114000004: {'dungeon_id': 1555}},
        dungeon_instances={1557: {'map_config_id': 1555, 'source': {'record_id': 64689830}}})
    assert battle['encounter']['title'] == 'Battle'
    assert battle['encounter']['trees'][0]['level_play_id'] == 114000004
    assert 'transition' not in battle
    assert exit_block['transition']['kind'] == 'dungeon_exit'
    assert 'encounter' not in exit_block
    assert battle['id'] != exit_block['id']


@pytest.mark.asyncio
@pytest.mark.parametrize('instance_rows', [[],
    [(1, {'Id': 1557, 'MapConfigId': 1555}, 10), (2, {'Id': 1557, 'MapConfigId': 1555}, 11)],
    [(1, {'Id': 1557, 'MapConfigId': '1555'}, 10), (2, {'Id': 1557, 'MapConfigId': 1555}, 11)],
    [(1, {'Id': 1558, 'MapConfigId': 1555}, 10)],
])
async def test_loader_fails_closed_for_missing_ambiguous_or_wrong_instance(monkeypatch, instance_rows):
    nodes, records = quest()
    session = SimpleNamespace(scalars=AsyncMock(side_effect=[
        [SimpleNamespace(nodes=nodes, asset_version='3.7.1', download_id='client')],
        [{'Key': '114000031_94', 'Data': records[94]}],
    ]), execute=AsyncMock(side_effect=[
        result(['row_index', 'data', 'id']), result(['row_index', 'data', 'id'], instance_rows),
        result(['state_key', 'node_id']), result(['node_id', 'quest_state_node_id', 'action_index']),
    ]))
    monkeypatch.setattr(tree_order, 'source_parents', AsyncMock(return_value=[]))
    monkeypatch.setattr(tree_order, 'media_links', AsyncMock(return_value={}))
    order = await tree_order.authored_order(session, 114000031, 21)
    block, = order['blocks']
    assert 'transition' not in block
    assert block['encounter']['status'] == 'unresolved'
    assert block['encounter']['trees'] == []
