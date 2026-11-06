from wuwa_story.ingestion.npc_scenario import scenario_actor_edges
from wuwa_story.ingestion.npc_sources import normalize_npcs


def test_shared_speaker_name_key_never_merges_blueprints_or_instances():
    blueprints = [{"Id": i, "BlueprintType": str(i), "EntityType": "Npc"} for i in (1, 2)]
    templates = [{"BlueprintType": str(i), "ComponentsData": {"BaseInfoComponent": {
        "Category": {"MainType": "Npc"}, "TidName": "Speaker_7_Name"}}} for i in (1, 2)]
    instances = [{"BlueprintType": "1", "MapId": i, "EntityId": i,
                  "ComponentsData": {"BaseInfoComponent": {"TidName": key}}}
                 for i, key in ((10, "Speaker_7_Name"), (20, "Speaker_8_Name"))]
    rows = normalize_npcs(blueprints, templates, instances, {}, speakers=[{"Id": 7}, {"Id": 8}])
    assert [row["id"] for row in rows] == ["character:npc:1", "character:npc:2"]
    assert all(row["npc"]["speakers"] == [] for row in rows)
    assert {v["name"]["key"] for v in rows[0]["npc"]["name_variants"]} == {
        "Speaker_7_Name", "Speaker_8_Name"}


def test_same_entity_id_for_different_blueprints_is_not_identity_evidence():
    npcs = [(f"character:npc:{i}", {"blueprint_type": str(i), "instances": [
        {"map_id": i, "entity_id": 10}]}) for i in (1, 2)]
    row = {"StateKey": "x_1_1", "Actions": [{"Name": "BeginFlowTemplate", "Params": {
        "Actors": [{"EntityId": 10, "TalkerId": 7}]}}]}
    assert list(scenario_actor_edges(npcs, [(0, row)], "3.7", {10: {"1", "2"}})) == []


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
