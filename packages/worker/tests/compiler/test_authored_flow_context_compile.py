import json

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.references import compile_reference_tables


def test_future_compiler_emits_possible_wrapper_context_from_dungeon_anchor(tmp_path):
    tables = {
        'QuestNodeData/questnodedata.json': [{'Key': '42_3', 'Data': {'Condition': {
            'Type': 'FinishDungeon', 'Condition': {'Type': 'DungeonId', 'DungeonId': 1574}}}}],
        'instance_dungeon/instancedungeon.json': [{'Id': 1574}],
        'LevelPlayData/levelplaydata.json': [{'LevelPlayId': 12, 'Data': {
            'Id': 12, 'Type': 'Dungeon', 'LevelId': 1574}}],
        'LevelPlayNodeData/levelplaynodedata.json': [{'Key': '12_40', 'Data': {'Id': 40,
                                                                               'Condition': {'Type': 'PlayFlow', 'Flow': {'FlowListName': 'Story', 'FlowId': 6, 'StateId': 19}}}}],
        'flow/flow.json': [{'Id': 'Story_6', 'States': [19, 20]}],
        'flowState/flowstate.json': [{'Id': 19, 'StateKey': 'Story_6_19', 'Actions': '[]'},
                                     {'Id': 20, 'StateKey': 'Story_6_20', 'Actions': json.dumps([
                                         {'Name': 'PlayMovie', 'Params': {'VideoName': 'M0357'}}])}],
    }
    for name, rows in tables.items():
        path = tmp_path / 'BinData' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding='utf-8')
    writer = Writer(tmp_path / 'out', 'fixture')
    for kind, key in [('quest', 'quest:42'), ('quest_node', 'quest_node:42:3'),
                      ('flow', 'flow:Story_6'), ('flow_state',
                                                 'flow_state:Story_6_19'),
                      ('flow_state', 'flow_state:Story_6_20')]:
        writer.emit(kind, {'id': key, 'source': {}, 'raw': {}})
    counts = compile_reference_tables(tmp_path, writer)
    writer.finish()
    edges = [json.loads(line) for line in (
        tmp_path / 'out/graphs/global.jsonl').read_text().splitlines()]
    link, = [edge for edge in edges if edge.get(
        'context_basis') == 'same_authored_flow_group']
    assert counts['authored_flow_context_links'] == 1
    assert link['from'] == 'quest:42' and link['to'] == 'flow_state:Story_6_20'
    assert link['relation'] == 'inference' and link['confidence'] == 'possible'
    assert json.loads(link['basis'])[
        'anchors'][0]['type'] == 'references_dungeon_flow_state'
    assert not any(edge['to'] == link['to'] and edge['type'] == 'references_flow_state'
                   and edge['from'].startswith('quest') for edge in edges)
