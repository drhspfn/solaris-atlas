import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData

from wuwa_story.ingestion.quest_tree_order import tree_node_order_hash
from wuwa_story.ingestion.spine_presentation import flow_presentation
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


def related_encounter_session(mutation=None):
    state = 'Story_28_1'
    quest = {
        231: {'Id': 231, 'Type': 'ChildQuest', 'ParentNodeId': 86,
              'Condition': {'Type': 'FinishDungeon', 'Condition': {
                  'Type': 'DungeonId', 'DungeonId': 1555, 'Count': 1}}},
        233: {'Id': 233, 'Type': 'Sequence', 'ParentNodeId': 0},
    }
    client_quest = deepcopy(quest)
    client_quest[233]['OccupationConfig'] = {'changed': True}
    client_quest[286] = {'Id': 286, 'Type': 'ChildQuest', 'ParentNodeId': 233}
    qnodes = nodes(client_quest, [(231, [10, 2]), (233, []), (286, [11])])
    for node in qnodes:
        node.update(parent_id=client_quest[node['id']]['ParentNodeId'],
                    type=client_quest[node['id']]['Type'])
    client = {'asset_version': '3.7.1', 'download_id': 'exact-client'}
    qtree = SimpleNamespace(nodes=qnodes, **client)
    # Complete 37-node local tree, including the historical M0334 invoking node66.
    level = {1: {'Id': 1, 'Type': 'Sequence', 'ParentNodeId': 0}}
    for identity in range(40, 76):
        level[identity] = {'Id': identity, 'Type': 'ChildQuest', 'ParentNodeId': 1}
    level[66]['Condition'] = {'Type': 'PlayFlow', 'Flow': {
        'FlowListName': 'Story', 'FlowId': 28, 'StateId': 1}}
    lnodes = nodes(level, [(1, []), *[(identity, [index]) for index, identity in enumerate(range(40, 76))]])
    for node in lnodes:
        node.update(parent_id=level[node['id']]['ParentNodeId'], type=level[node['id']]['Type'])
    ltree = SimpleNamespace(tree_id=114000004, nodes=lnodes, **client)
    membership = {'LevelPlayId': 114000004, 'Data': {
        'Id': 114000004, 'Type': 'Dungeon', 'LevelId': 1555}}
    raw_flow = {'StateKey': state, 'Id': 1, 'Actions': [{'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': '/Game/Aki/M0334/DA_M0334.DA_M0334', 'SequenceNames': ['M0334_1Z']}}]}
    if mutation == 'dungeon':
        quest[231]['Condition']['Condition']['DungeonId'] = 1556
    elif mutation == 'flow':
        level[66]['Condition']['Flow']['FlowId'] = 29
    elif mutation == 'node_id':
        level[66]['Id'] = 67
    elif mutation == 'missing_node':
        del level[75]
    elif mutation == 'membership':
        membership['Data']['LevelId'] = 1556
    queries = []

    def sql(statement):
        value = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        queries.append(value)
        return value

    async def scalars(statement):
        value = sql(statement)
        if 'FROM core.authored_tree_order' in value:
            return [qtree] if "tree_kind = 'quest'" in value else [ltree]
        if 'QuestNodeData' in value:
            return [{'Key': f'114000031_{i}', 'Data': record} for i, record in quest.items()]
        if 'LevelPlayNodeData' in value:
            return [{'Key': f'114000004_{i}', 'Data': record} for i, record in level.items()]
        if 'LevelPlayData' in value:
            return [membership]
        raise AssertionError(value)

    async def execute(statement):
        value = sql(statement)
        if 'LevelPlayData' in value:
            return [(4322, membership, 35603680)] if membership['Data']['LevelId'] == 1555 else []
        if 'FROM core.quest_state' in value:
            return IteratorResult(SimpleResultMetaData(['key', 'id']), iter([(state, 4648369)]))
        if 'flowState' in value:
            return [(raw_flow, 35940570, 11812)]
        if 'QuestNodeData' in value:
            return [(35708123, 18477, '114000031_231')]
        raise AssertionError(value)

    return SimpleNamespace(scalars=AsyncMock(side_effect=scalars), execute=AsyncMock(side_effect=execute)), queries, raw_flow


@pytest.mark.asyncio
async def test_historical_encounter_survives_unrelated_quest_changes_without_global_order():
    session, queries, raw_flow = related_encounter_session()
    result = await tree_order.authored_order(session, 114000031, 15)
    assert result['ordering'] == 'related'
    assert result['positions'] == {} and result['mapped_flow_states'] == []
    assert result['state_ids'] == [4648369]
    block, = result['blocks']
    assert block['position'] is None and block['branches'] == []
    assert block['flow_states'] == block['media'] == block['interactions'] == []
    encounter = block['encounter']
    assert encounter['quest_position'] is None and encounter['quest_order'] == 'unknown'
    assert encounter['release_id'] == 15
    assert encounter['client'] == {'asset_version': '3.7.1', 'download_id': 'exact-client'}
    assert encounter['source']['record_id'] == 35708123
    assert encounter['source']['release_id'] == 15
    tree, = encounter['trees']
    assert tree['membership']['record_id'] == 35603680
    assert tree['ordering'] == 'authored_tree'
    assert len(tree['root']['children']) == 36
    flow, = [child['flow_states'][0] for child in tree['root']['children'] if child['flow_states']]
    assert flow['state_node_id'] == 4648369
    assert flow['presentation_sha256'] == flow_presentation(raw_flow)['sha256']
    assert flow['cinematics'][0]['source']['record_id'] == 35940570
    assert flow['cinematics'][0]['source']['release_id'] == 15
    # Every raw lookup remains snapshot scoped; no latest-release borrowing.
    raw_queries = [query for query in queries if 'FROM raw.source_record' in query]
    assert raw_queries and all('source_record.release_id = 15' in query and 'source_file.release_id = 15' in query for query in raw_queries)
    assert all('Key' in query or 'StateKey' in query or 'LevelId' in query or 'LevelPlayId' in query for query in raw_queries)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['dungeon', 'flow', 'node_id', 'missing_node', 'membership'])
async def test_related_encounter_rejects_changed_identity_or_incomplete_level_tree(mutation):
    session, _, _ = related_encounter_session(mutation)
    assert await tree_order.authored_order(session, 114000031, 15) is None


@pytest.mark.asyncio
async def test_related_state_is_visible_in_media_manifest_without_global_position(monkeypatch):
    from wuwa_story.api.routes.story import media

    source_session, _, _ = related_encounter_session()
    order = await tree_order.authored_order(source_session, 114000031, 15)
    monkeypatch.setattr(media.read_projections, 'should_route_to_projection', lambda *args, **kwargs: False)
    monkeypatch.setattr(media, 'get_quest', AsyncMock(return_value=SimpleNamespace(node_id=4688653, game_quest_id=114000031)))
    monkeypatch.setattr(media, '_release_id', AsyncMock(return_value=15))
    monkeypatch.setattr(media, 'authored_order', AsyncMock(return_value=order))
    monkeypatch.setattr(media, '_quest_page_state_ids', AsyncMock(return_value=[]))
    action = SimpleNamespace(node_id=99, action_index=1)
    state = SimpleNamespace(node_id=4648369, state_key='Story_28_1')
    reference = 'asset:ue:/Game/Aki/M0334/DA_M0334.DA_M0334'

    async def links(_session, identities, relations, release):
        assert release == 15
        if 99 in identities and 'plays_sequence_asset' in relations:
            return {99: [{'relation': 'plays_sequence_asset', 'node_id': 100,
                          'canonical_key': reference, 'basis': 'ShowTalk.Params.SequenceDataAsset',
                          'source': {'record_id': 35940570, 'release_id': 15}}]}
        return {}

    monkeypatch.setattr(media, '_links', links)
    for name in ['cutscene_flows', 'cutscene_videos', 'caption_texts']:
        monkeypatch.setattr(media, name, AsyncMock(return_value={}))
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=None),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(action, state, 'quest_action:Story_28_1:1')])),
        get=AsyncMock(return_value=SimpleNamespace(game_version='3.1.0')))
    result = await media.quest_media('114000031', '3.1.0', 'en', session)
    event, = result['events']
    assert result['game_version'] == '3.1.0'
    assert event['reference'] == reference and event['kind'] == 'sequence'
    assert event['source'] == {'record_id': 35940570, 'release_id': 15}
    assert not event['story_order_mapped'] and not event['context_inferred']
    assert 'story_position' not in event
    query = str(session.execute.await_args.args[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
    assert '4648369' in query  # Included through related encounter despite empty original quest states.
