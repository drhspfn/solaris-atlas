from wuwa_story.ingestion.runtime_media import quest_runtime_links, runtime_media
from wuwa_story.story.runtime_blocks import project_blocks


def nodes(records, paths):
    return [{'id': identity, 'parent_id': records[identity].get('ParentNodeId', 0),
             'type': records[identity]['Type'], 'path': path} for identity, path in paths]


def test_travel_mp4_is_an_exact_reference_even_without_playmovie():
    raw = {'FinishActions': [{'Name': 'TeleportDungeon', 'Params': {'DungeonId': 1574,
        'TransitionOption': {'Type': 'PlayMp4', 'Mp4Path': 'M0355_Loading'}}}]}
    refs = list(runtime_media(raw, '$.Data'))
    assert refs == [{'kind': 'cutscene', 'reference': 'cutscene:M0355_Loading',
        'raw_path': '$.Data.FinishActions[0].Params.TransitionOption.Mp4Path',
        'basis': 'PlayMp4.Mp4Path = CgName', 'transition': True}]
    assert list(runtime_media({'Mp4Path': 'NotEnoughEvidence'})) == []
    rows = [(42, {'Key': '121000040_119', 'Data': raw})]
    assert list(quest_runtime_links(rows, set())) == []
    link, = quest_runtime_links(rows, {'cutscene:M0355_Loading'})
    assert link['from'] == 'quest_node:121000040:119'
    assert link['raw_path'] == '$[42].Data.FinishActions[0].Params.TransitionOption.Mp4Path'
    assert link['relation'] == 'explicit_reference'


def test_levelplay_objectives_keep_conditions_branches_and_media_between_flows():
    quest = {1: {'Id': 1, 'Type': 'Sequence'}, 2: {'Id': 2, 'ParentNodeId': 1,
        'Type': 'ChildQuest', 'TidTip': 'Explore', 'Condition': {'Type': 'CheckLevelPlay',
        'LevelPlayIds': [183], 'CompleteNumber': 1}}}
    level = {1: {'Id': 1, 'Type': 'Sequence'}, 2: {'Id': 2, 'ParentNodeId': 1,
        'Type': 'ParallelSelect'}, 3: {'Id': 3, 'ParentNodeId': 2, 'Type': 'ChildQuest',
        'TidTip': 'OpenFridge', 'Condition': {'Type': 'DoInteract'},
        'FinishActions': [{'Name': 'PlayFlow', 'Params': {'FlowListName': 'Story', 'FlowId': 1, 'StateId': 33}}]},
        4: {'Id': 4, 'ParentNodeId': 1, 'Type': 'ChildQuest', 'TidTip': 'EnterHall',
        'Condition': {'Type': 'ReachArea'}, 'FinishActions': [{'Name': 'PlayMovie',
        'Params': {'VideoName': 'M0366'}}, {'Name': 'PlaySequenceData',
        'Params': {'Path': '/Game/Lab/DA_Bubble1.DA_Bubble1'}}]}}
    blocks = project_blocks(119, nodes(quest, [(1, []), (2, [0])]), quest,
        {183: (nodes(level, [(1, []), (2, [0]), (3, [0, 0]), (4, [1])]), level)})
    assert [block['text_key'] for block in blocks] == ['Explore', 'OpenFridge', 'EnterHall']
    fridge, hall = blocks[1:]
    assert fridge['branches'] == [{'id': 'level_play_node:183:2', 'type': 'ParallelSelect'}]
    assert hall['branches'] == []
    assert fridge['flow_states'][0]['state'] == 'Story_1_33'
    assert fridge['position'] < fridge['flow_states'][0]['position'] < hall['position']
    assert [ref['kind'] for ref in hall['media']] == ['cutscene', 'sequence']
    assert hall['media'][0]['position'] < hall['media'][1]['position']
    assert hall['source']['key'] == '183_4'
    assert len({block['id'] for block in blocks}) == len(blocks)


def test_ambiguous_completion_does_not_flatten_multiple_levelplays():
    raw = {1: {'Id': 1, 'Type': 'ChildQuest', 'TidTip': 'Explore', 'Condition': {
        'Type': 'CheckLevelPlay', 'LevelPlayIds': [183, 184], 'CompleteNumber': 1}}}
    assert len(project_blocks(119, nodes(raw, [(1, [])]), raw,
        {183: (nodes(raw, [(1, [])]), raw)})) == 1
