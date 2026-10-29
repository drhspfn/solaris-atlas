import hashlib
import json

from wuwa_story.story.tree_order import flow_positions


def nodes(records, paths):
    return [{'id': identity, 'path': path, 'record_sha256': hashlib.sha256(json.dumps(
        records[identity], sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode()).hexdigest()} for identity, path in paths]


def flow(state):
    return {'FlowListName': 'Cave', 'FlowId': 1, 'StateId': state}


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
