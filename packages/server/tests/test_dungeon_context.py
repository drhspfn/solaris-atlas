import json

import pytest

from wuwa_story.ingestion.dungeon_context import dungeon_links, level_memberships, requirements


def fixture():
    condition = {"Type": "FinishDungeon", "Condition": {
        "Type": "DungeonId", "DungeonId": 1518}}
    quests = [
        (12, {"Key": "158800019_138", "Data": json.dumps({"Condition": condition})})]
    levels = [(7, {"LevelPlayId": 153000005, "Data": {
               "Id": 153000005, "LevelId": 1518, "Type": "Quest"}})]
    nodes = [(14, {"Key": "153000005_127", "Data": {"Id": 127, "FinishActions": [
        {"Name": "PlayFlow", "Params": {
            "FlowListName": "Boss", "FlowId": 1, "StateId": 43}}
    ]}})]
    return quests, levels, nodes


def test_dungeon_boss_context_has_exact_source_chain_without_inventing_chronology():
    quests, levels, nodes = fixture()
    required = requirements(quests)
    membership = level_memberships(levels, required)
    links = list(dungeon_links(required, membership, nodes, {"Boss_1_43"}))
    assert len(links) == 1
    link = links[0]
    assert (link["from"], link["to"]) == (
        "quest_node:158800019:138", "flow_state:Boss_1_43")
    evidence = json.loads(link["basis"])
    assert evidence["quest_condition"]["row"] == 12
    assert evidence["level_play"]["row"] == 7
    assert evidence["flow_source"]["row"] == 14
    assert "not proof every branch occurred" in evidence["meaning"]


def test_unrelated_level_preconditions_unknown_states_and_reused_ids_do_not_establish_ownership():
    quests, levels, nodes = fixture()
    required = requirements(quests)
    assert list(dungeon_links(required, level_memberships(
        levels, required), nodes, set())) == []
    levels[0][1]["Data"]["LevelId"] = 8
    assert level_memberships(levels, required) == {}
    quests[0][1]["Data"] = {"Condition": {
        "Type": "PreQuest", "DungeonId": 1518}}
    assert requirements(quests) == {}
    nodes[0][1]["Data"]["Id"] = 99
    assert list(dungeon_links(
        required, {153000005: {"dungeon_id": 1518}}, nodes, {"Boss_1_43"})) == []


@pytest.mark.parametrize("level_type", ["Quest", "Dungeon"])
def test_finish_dungeon_owns_explicit_playflow_conditions_in_both_level_types(level_type):
    state = "剧情_3_1_拉海洛主线_上半_3_28_1"
    quests = [(19042, {"Key": "114000031_231", "Data": {
        "Condition": {"Type": "FinishDungeon", "Condition": {
            "Type": "DungeonId", "Count": 1, "DungeonId": 1555, "IsExitDungeon": True,
        }},
    }})]
    levels = [(4338, {"LevelPlayId": 114000004, "Data": {
        "Id": 114000004, "LevelId": 1555, "Type": level_type,
    }})]
    nodes = [(65884, {"Key": "114000004_66", "Data": {
        "Id": 66, "Type": "ChildQuest", "ParentNodeId": 32,
        "Condition": {"Type": "PlayFlow", "Flow": {
            "FlowListName": "剧情_3_1_拉海洛主线_上半_3", "FlowId": 28, "StateId": 1,
        }},
    }})]
    required = requirements(quests)
    links = list(dungeon_links(required, level_memberships(
        levels, required), nodes, {state}))
    assert len(links) == 1
    assert (links[0]["from"], links[0]["to"]) == (
        "quest_node:114000031:231", f"flow_state:{state}")
    evidence = json.loads(links[0]["basis"])
    assert evidence["quest_condition"]["path"] == "$[19042].Data.Condition"
    assert evidence["level_play"]["row"] == 4338
    assert evidence["flow_source"] == {
        "file": "BinData/LevelPlayNodeData/levelplaynodedata.json", "row": 65884,
        "path": "$[65884].Data.Condition.Flow",
    }
    # Display labels, state prefixes and parent node IDs do not create ownership.
    assert list(dungeon_links(required, {}, nodes, {state})) == []
    assert list(dungeon_links(required, level_memberships(
        levels, required), nodes, set())) == []


@pytest.mark.parametrize("level_type", ["World", "Custom", None])
def test_unrecognized_level_types_do_not_become_dungeon_members(level_type):
    _, levels, _ = fixture()
    levels[0][1]["Data"]["Type"] = level_type
    assert level_memberships(levels, {1518}) == {}


@pytest.mark.parametrize("condition", [
    {"Type": "Other", "Flow": {"FlowListName": "Boss", "FlowId": 1, "StateId": 43}},
    {"Type": "PlayFlow", "Flow": {"FlowListName": "Boss", "FlowId": 1}},
    {"Type": "PlayFlow", "Flow": {"FlowListName": "Boss", "FlowId": True, "StateId": 43}},
    {"Type": "PlayFlow", "Params": {"FlowListName": "Boss", "FlowId": 1, "StateId": 43}},
])
def test_condition_requires_typed_complete_flow_binding(condition):
    quests, levels, nodes = fixture()
    nodes[0][1]["Data"] = {"Id": 127, "Condition": condition}
    required = requirements(quests)
    assert list(dungeon_links(required, level_memberships(
        levels, required), nodes, {"Boss_1_43"})) == []


def test_nested_json_conditions_and_dungeon_actions_preserve_exact_paths():
    quests, levels, nodes = fixture()
    levels[0][1]["Data"]["Type"] = "Dungeon"
    nodes[0][1]["Data"]["Condition"] = {"Type": "Or", "Conditions": [
        {"Type": "PlayFlow", "Flow": {
            "FlowListName": "Boss", "FlowId": 2, "StateId": 4}},
    ]}
    nodes[0][1]["Data"] = json.dumps(nodes[0][1]["Data"])
    required = requirements(quests)
    links = list(dungeon_links(required, level_memberships(
        levels, required), nodes, {"Boss_1_43", "Boss_2_4"}))
    assert {json.loads(link["basis"])["flow_source"]["path"] for link in links} == {
        "$[14].Data.FinishActions[0].Params", "$[14].Data.Condition.Conditions[0].Flow",
    }
