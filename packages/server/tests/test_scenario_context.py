from wuwa_story.ingestion.scenario_context import scenario_links


def test_quest_level_play_condition_finds_triggered_movie_state():
    quests = [(10, {"Key": "119_120", "Data": {"Condition": {
        "Type": "CheckLevelPlay", "LevelPlayIds": [183], "CompleteNumber": 1}}})]
    levels = [(11, {"Key": "183_138", "Data": {"Id": 138, "Condition": {
        "Type": "ReachArea"}, "FinishActions": [{"Name": "PlayFlow", "Params": {
            "FlowListName": "Cave", "FlowId": 1, "StateId": 39}}]}})]
    links = list(scenario_links(quests, levels, {"Cave_1_39"}))
    assert [(link["from"], link["to"]) for link in links] == [("quest:119", "flow_state:Cave_1_39")]
    assert "transcript position are not established" in links[0]["basis"]
    quests[0][1]["Data"]["Condition"]["LevelPlayIds"] = [183, 184]
    assert list(scenario_links(quests, levels, {"Cave_1_39"})) == []


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
