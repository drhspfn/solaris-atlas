from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from wuwa_story.api.routes.story import map as story_map


@pytest.mark.asyncio
async def test_side_quests_without_chapters_remain_in_story_map(monkeypatch: pytest.MonkeyPatch) -> None:
    async def records(*_args: object) -> list[object]:
        return []

    async def titles(*_args: object) -> dict[str, str]:
        return {"side-first": "First side quest", "side-second": "Second side quest"}

    async def first_observed(*_args: object) -> dict[int, str]:
        return {10: "1.0.0", 11: "1.0.0"}

    monkeypatch.setattr(story_map, "_records", records)
    monkeypatch.setattr(story_map, "_localized_titles", titles)
    monkeypatch.setattr(story_map, "_first_observed", first_observed)
    quests = [
        SimpleNamespace(data={"QuestId": 11, "Data": {"Type": 2, "TidName": "side-second",
                                                      "ProvideType": {"Conditions": [{"Type": "PreQuest", "PreQuest": 10}]}}}, row_index=1),
        SimpleNamespace(data={"QuestId": 10, "Data": {
                        "Type": 2, "TidName": "side-first"}}, row_index=0),
        SimpleNamespace(data={"QuestId": 12, "Data": {
                        "Type": 1, "ChapterId": 5}}, row_index=2),
    ]

    result = await story_map._prerequisite_map(
        None, SimpleNamespace(id=1, game_version="1.0.0"), "en", 2, [
            "1.0.0"], quests
    )

    assert len(result["chapters"]) == 1
    chapter = result["chapters"][0]
    assert chapter["id"] == 0
    assert chapter["title"] == "Quests without a chapter"
    assert [node["id"] for node in chapter["nodes"]] == [10, 11]
    assert chapter["nodes"][1]["previous_node_ids"] == [10]
    assert chapter["nodes"][0]["quests"][0]["source"]["raw_path"] == "$[0]"

    all_paths = await story_map._prerequisite_map(
        None, SimpleNamespace(id=1, game_version="1.0.0"), "en", 0, ["1.0.0"], quests
    )
    assert [chapter["id"] for chapter in all_paths["chapters"]] == [5, 0]


@pytest.mark.asyncio
async def test_version_filter_keeps_only_quests_first_seen_in_that_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases = [SimpleNamespace(id=1, sequence=1, game_version="1.0.0"),
                SimpleNamespace(id=2, sequence=2, game_version="1.1.0")]

    class Session:
        async def scalar(self, _query: object) -> int:
            return 1

        async def scalars(self, _query: object) -> list[object]:
            return releases

    async def snapshot(_session: object, release: object, *_args: object) -> dict[str, object]:
        version = release.game_version
        quests = [{"game_quest_id": 10, "first_observed_game_version": "1.0.0"}]
        if version == "1.1.0":
            quests.append({"game_quest_id": 11, "first_observed_game_version": "1.1.0"})
        return {"selected_game_version": version, "tree_available": False,
                "chapters": [{"id": 5, "title": "Chapter", "nodes": [
                    {"id": quest["game_quest_id"], "quests": [quest]} for quest in quests
                ]}]}

    monkeypatch.setattr(story_map, "_snapshot_map", snapshot)
    session = Session()
    all_patches = await story_map.story_map("en", None, 1, session)
    selected = await story_map.story_map("en", "1.1.0", 1, session)

    assert all_patches["selected_game_version"] is None
    assert [chapter["snapshot_game_version"] for chapter in all_patches["chapters"]] == [
        "1.0.0", "1.1.0"
    ]
    assert [node["id"] for chapter in all_patches["chapters"] for node in chapter["nodes"]] == [10, 11]
    assert [node["id"] for chapter in selected["chapters"] for node in chapter["nodes"]] == [11]
    assert selected["chapters"][0]["snapshot_game_version"] == "1.1.0"
    with pytest.raises(HTTPException) as error:
        await story_map.story_map("en", "2.0.0", 1, session)
    assert error.value.status_code == 404
