import json

from wuwa_story.ingestion.dungeon_context import dungeon_links, level_memberships, requirements


def fixture():
    condition = {"Type": "FinishDungeon", "Condition": {"Type": "DungeonId", "DungeonId": 1518}}
    quests = [(12, {"Key": "158800019_138", "Data": json.dumps({"Condition": condition})})]
    levels = [(7, {"LevelPlayId": 153000005, "Data": {"Id": 153000005, "LevelId": 1518, "Type": "Quest"}})]
    nodes = [(14, {"Key": "153000005_127", "Data": {"Id": 127, "FinishActions": [
        {"Name": "PlayFlow", "Params": {"FlowListName": "Boss", "FlowId": 1, "StateId": 43}}
    ]}})]
    return quests, levels, nodes


def test_dungeon_boss_context_has_exact_source_chain_without_inventing_chronology():
    quests, levels, nodes = fixture()
    required = requirements(quests)
    membership = level_memberships(levels, required)
    links = list(dungeon_links(required, membership, nodes, {"Boss_1_43"}))
    assert len(links) == 1
    link = links[0]
    assert (link["from"], link["to"]) == ("quest_node:158800019:138", "flow_state:Boss_1_43")
    evidence = json.loads(link["basis"])
    assert evidence["quest_condition"]["row"] == 12
    assert evidence["level_play"]["row"] == 7
    assert evidence["flow_source"]["row"] == 14
    assert "not proof every branch occurred" in evidence["meaning"]


def test_unrelated_level_preconditions_unknown_states_and_reused_ids_do_not_establish_ownership():
    quests, levels, nodes = fixture()
    required = requirements(quests)
    assert list(dungeon_links(required, level_memberships(levels, required), nodes, set())) == []
    levels[0][1]["Data"]["LevelId"] = 8
    assert level_memberships(levels, required) == {}
    quests[0][1]["Data"] = {"Condition": {"Type": "PreQuest", "DungeonId": 1518}}
    assert requirements(quests) == {}
    nodes[0][1]["Data"]["Id"] = 99
    assert list(dungeon_links(required, {153000005: {"dungeon_id": 1518}}, nodes, {"Boss_1_43"})) == []
