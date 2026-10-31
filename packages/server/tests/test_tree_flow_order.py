import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData

from wuwa_story.ingestion.quest_tree_order import tree_node_order_hash
from wuwa_story.story import tree_order
from wuwa_story.story.tree_order import flow_positions, records_match


def nodes(records, paths):
    return [{'id': identity, 'path': path, 'record_sha256': hashlib.sha256(json.dumps(
        records[identity], sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode()).hexdigest()} for identity, path in paths]


def flow(state):
    return {'FlowListName': 'Cave', 'FlowId': 1, 'StateId': state}


@pytest.mark.asyncio
async def test_authored_order_includes_cutscene_caption_states(monkeypatch):
    records = {1: {'EnterActions': [flow(1)]}}
    tree_nodes = nodes(records, [(1, [])])
    tree_nodes[0].update(parent_id=0, type='ChildQuest')
    tree = SimpleNamespace(nodes=tree_nodes, asset_version='3.3.0', download_id='client')

    def result(columns, rows):
        # SQLAlchemy results expose keys(), so dict(result) incorrectly treats
        # them as mappings instead of consuming their two-column rows.
        return IteratorResult(SimpleResultMetaData(columns), iter(rows))

    session = SimpleNamespace(
        scalars=AsyncMock(side_effect=[[tree], [{'Key': '100_1', 'Data': records[1]}]]),
        execute=AsyncMock(side_effect=[
            result(['state_key', 'node_id'], [('Cave_1_1', 10)]),
            result(['node_id', 'quest_state_node_id', 'action_index'], [(20, 10, 3)]),
            result(['node_id', 'state_key'], [(40, 'Captions_1_1')]),
        ]),
    )
    monkeypatch.setattr(tree_order, 'source_parents', AsyncMock(return_value={(20, 10)}))
    monkeypatch.setattr(tree_order, 'media_links', AsyncMock(side_effect=[
        {20: [{'node_id': 30}]}, {30: [{'node_id': 40}]},
    ]))
    ordered = await tree_order.authored_order(session, 100, 33)
    assert ordered['state_ids'] == [10, 40]
    assert ordered['positions']['Captions_1_1'] == (*ordered['positions']['Cave_1_1'], 3, 0)


def test_quest_and_levelplay_sequences_place_movies_after_preceding_story():
    quest = {
        41: {'Condition': {'FlowListName': 'Story', 'FlowId': 1, 'StateId': 22}},
        120: {'Condition': {'Type': 'CheckLevelPlay', 'LevelPlayIds': [183], 'CompleteNumber': 1}},
    }
    level = {
        135: {'FinishActions': [{'Name': 'PlayFlow', 'Params': flow(14)}]},
        138: {'FinishActions': [{'Name': 'PlayFlow', 'Params': flow(39)}]},
    }
    qtree = nodes(quest, [(41, [42]), (120, [43, 0])])
    ltree = nodes(level, [(135, [7, 0, 8]), (138, [7, 0, 11])])
    valid = {'Story_1_22', 'Cave_1_14', 'Cave_1_39'}
    ordered = flow_positions(qtree, quest, {183: (ltree, level)}, valid)
    assert [key for key, _ in ordered] == ['Story_1_22', 'Cave_1_14', 'Cave_1_39']
    level[138]['FinishActions'] = []
    assert [key for key, _ in flow_positions(qtree, quest, {183: (ltree, level)}, valid)] == ['Story_1_22']


def test_parent_finish_actions_follow_children():
    quest = {
        1: {'EnterActions': [flow(1)], 'FinishActions': [flow(3)]},
        2: {'EnterActions': [flow(2)]},
    }
    order = flow_positions(nodes(quest, [(1, []), (2, [0])]), quest, {},
                           {'Cave_1_1', 'Cave_1_2', 'Cave_1_3'})
    assert [key for key, _ in order] == ['Cave_1_1', 'Cave_1_2', 'Cave_1_3']


def test_tree_match_ignores_runtime_repair_but_not_order_or_flow_changes():
    client = {'Id': 1, 'Type': 'ChildQuest', 'ParentNodeId': 0,
              'Condition': {'Type': 'CheckLevelPlay', 'LevelPlayIds': [183]},
              'FixProcessor': {'FixActions': []}}
    node = nodes({1: client}, [(1, [])])[0]
    node['order_sha256'] = tree_node_order_hash(client)
    snapshot = {key: value for key, value in client.items() if key != 'FixProcessor'}
    assert records_match([node], {1: snapshot})
    snapshot['Condition'] = {'Type': 'CheckLevelPlay', 'LevelPlayIds': [184]}
    assert not records_match([node], {1: snapshot})
