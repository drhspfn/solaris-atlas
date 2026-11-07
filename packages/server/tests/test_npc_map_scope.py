import json

from wuwa_story.ingestion.npc_map_scope import flow_map_context


def level(identity, map_id):
    return (0, {"LevelPlayId": identity, "Data": {"Id": identity, "LevelId": map_id}})


def caller(identity, name="Story", flow=1, state=36):
    return (0, {"Key": f"{identity}_327", "Data": json.dumps({"Actions": [{
        "Name": "PlayFlow", "Params": {"FlowListName": name, "FlowId": flow, "StateId": state}}]})})


def test_exact_levelplay_map_propagates_only_to_its_called_flow():
    assert flow_map_context([level(183000000, 909), level(42, 906)], [caller(183000000)]) == {
        "Story_1_36": {909}}


def test_conflicting_callers_remain_visible_as_ambiguous_map_context():
    assert flow_map_context([level(1, 909), level(2, 906)], [caller(1), caller(2)]) == {
        "Story_1_36": {906, 909}}


def test_missing_or_inconsistent_map_source_does_not_invent_context():
    assert flow_map_context([], [caller(1)]) == {}
    assert flow_map_context([(0, {"LevelPlayId": 1, "Data": {"Id": 2, "LevelId": 909}})], [caller(1)]) == {}
    assert flow_map_context([level(1, 909)], [(0, {"Key": "invalid", "Data": {}})]) == {}


def test_nested_conditional_calls_and_encoded_level_data_are_supported():
    source = level(1, 909)
    source[1]["Data"] = json.dumps(source[1]["Data"])
    node = caller(1)[1]
    node["Data"] = {"Branches": [json.loads(node["Data"])]}
    assert flow_map_context([source], [(0, node)]) == {"Story_1_36": {909}}
