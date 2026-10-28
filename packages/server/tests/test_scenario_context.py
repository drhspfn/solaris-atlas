from wuwa_story.ingestion.scenario_context import scenario_links


def test_unique_scenario_progress_and_flow_list_gives_context_not_traversal():
    quests = [(1, {"Key": "42_3", "Data": {"FinishActions": [
        {"Name": "SetVar", "Params": {"VarLeft": {
            "Source": "Global", "Keyword": "story-progress"}}},
        {"Name": "PlayFlow", "Params": {
            "FlowListName": "Story", "FlowId": 1, "StateId": 1}},
    ]}})]
    levels = [(2, {"Key": "123_7", "Data": {"Id": 7, "Condition": {
        "Type": "CompareVar", "Var1": {"Source": "Global", "Keyword": "story-progress"}}}}),
        (3, {"Key": "123_8", "Data": {"Id": 8, "Condition": {"Type": "DoInteract", "Option": {
            "FlowListName": "Story", "FlowId": 35, "StateId": 2}}}})]
    links = list(scenario_links(quests, levels, {"Story_35_2"}))
    assert len(links) == 1
    assert links[0]["from"] == "quest:42"
    assert links[0]["to"] == "flow_state:Story_35_2"
    assert "not proof of runtime traversal" in links[0]["basis"]
    assert list(scenario_links(quests, levels, set())) == []
    levels[1][1]["Data"]["Condition"]["Option"]["FlowListName"] = "Other"
    assert list(scenario_links(quests, levels, {"Other_35_2"})) == []
    levels[1][1]["Data"]["Condition"]["Option"]["FlowListName"] = "Story"
    quests.append((4, {"Key": "43_1", "Data": {"FinishActions": [
        {"Name": "SetVar", "Params": {"VarLeft": {
            "Source": "Global", "Keyword": "story-progress"}}}
    ]}}))
    assert list(scenario_links(quests, levels, {"Story_35_2"})) == []
