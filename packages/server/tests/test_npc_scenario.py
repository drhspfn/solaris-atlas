from wuwa_story.ingestion.npc_scenario import scenario_actor_edges


def test_actor_flow_and_adjacent_speech_are_context_specific():
    npc = ("character:npc:17288", {"blueprint_type": "iris", "instances": [
        {"map_id": 1, "entity_id": 152700001}, {"map_id": 2, "entity_id": 152700001}]})
    actions = [{"Name": "BeginFlowTemplate", "Params": {"Actors": [
        {"EntityId": 152700001, "TalkerId": 7}, {"EntityId": -1, "IsPlayer": True}]}},
        {"Name": "ShowTalk", "Params": {"TalkItems": [
            {"Type": "Talk", "WhoId": 7}, {"Type": "Talk", "WhoId": 8}]}},
        {"Name": "ShowTalk", "Params": {"TalkItems": [{"Type": "Talk", "WhoId": 7}]}}]
    rows = [(4, {"StateKey": "Test_1_2", "Actions": actions})]
    links = list(scenario_actor_edges([npc], rows, "3.7", {152700001: {"iris"}}))
    assert [(link["type"], link["to"]) for link in links] == [
        ("references_flow_state", "flow_state:Test_1_2"),
        ("presents_talk_item", "talk_item:Test_1_2:1:0")]
    assert all("speaker:" not in link["to"] for link in links)
    assert list(scenario_actor_edges([npc], rows, "3.7", {152700001: {"iris", "zahira"}})) == []


def test_duplicate_talker_is_not_own_speech_evidence():
    npc = ("character:npc:1", {"blueprint_type": "a", "instances": [{"entity_id": 10}]})
    row = {"StateKey": "x_1_2", "Actions": [
        {"Name": "BeginFlowTemplate", "Params": {"Actors": [
            {"EntityId": 10, "TalkerId": 4}, {"EntityId": 20, "TalkerId": 4}]}},
        {"Name": "ShowTalk", "Params": {"TalkItems": [{"Type": "Talk", "WhoId": 4}]}}]}
    edges = list(scenario_actor_edges([npc], [(0, row)], "3.7", {10: {"a"}}))
    assert [edge["type"] for edge in edges] == ["references_flow_state"]
