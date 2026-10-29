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
@pytest.mark.parametrize("has_new_quest", [False, True])
async def test_patch_filter_excludes_earlier_quests_and_all_patches_follow_order(
    monkeypatch: pytest.MonkeyPatch,
    has_new_quest: bool,
) -> None:
    releases = [SimpleNamespace(id=1, sequence=1, game_version="1.0.0"),
                SimpleNamespace(id=2, sequence=2, game_version="1.1.0")]

    class Session:
        async def execute(self, _query: object) -> list[object]:
            return []

        async def scalar(self, _query: object) -> int:
            return 1

        async def scalars(self, _query: object) -> list[object]:
            return releases

    async def snapshot(_session: object, release: object, *_args: object) -> dict[str, object]:
        version = release.game_version
        quests = [{"game_quest_id": 10, "first_observed_game_version": "1.0.0"}]
        if version == "1.1.0" and has_new_quest:
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
    expected_versions = ["1.0.0", "1.1.0"] if has_new_quest else ["1.0.0"]
    expected_quests = [10, 11] if has_new_quest else [10]
    assert [chapter["snapshot_game_version"] for chapter in all_patches["chapters"]] == expected_versions
    assert [node["id"] for chapter in all_patches["chapters"] for node in chapter["nodes"]] == expected_quests
    assert [node["id"] for chapter in selected["chapters"] for node in chapter["nodes"]] == ([11] if has_new_quest else [])
    if has_new_quest:
        assert selected["chapters"][0]["snapshot_game_version"] == "1.1.0"
        assert selected["chapters"][0]["tree_available"] is False
        assert selected["chapters"][0]["nodes"][0]["quests"][0]["first_observed_game_version"] == "1.1.0"
    with pytest.raises(HTTPException) as error:
        await story_map.story_map("en", "2.0.0", 1, session)
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("has_tree", [False, True])
async def test_batched_snapshot_matches_existing_snapshot_semantics(monkeypatch, has_tree):
    release = SimpleNamespace(id=2, game_version="2.1.0", sequence=2)
    quests = [SimpleNamespace(row_index=3, data={"QuestId": 10, "Data": {
        "Type": 1, "TidName": "quest-title", "ChapterId": 5,
    }})]
    tree = [SimpleNamespace(row_index=4, data={"Id": 7, "QuestType": 1,
        "ChapterId": 5, "Name": "node-title", "QuestArray": [10], "PreNode": []})] if has_tree else []
    chapters = [SimpleNamespace(row_index=2, data={"Id": 5, "Name": "chapter-title",
        "ChapterName": "chapter-title"})]
    records = {
        (2, story_map.QUEST_PATH): quests,
        (2, story_map.TREE_NODE_PATH): tree,
        (2, story_map.TREE_CHAPTER_PATH): chapters,
        (2, story_map.QUEST_CHAPTER_PATH): chapters,
    }
    titles = {"quest-title": "Quest", "node-title": "Node", "chapter-title": "Chapter"}

    async def load_records(_session, release_id, path):
        return records.get((release_id, path), [])

    async def load_titles(_session, _release, _locale, _keys):
        return titles

    async def load_first(_session, _release, _ids):
        return {10: "2.1.0"}

    monkeypatch.setattr(story_map, "_records", load_records)
    monkeypatch.setattr(story_map, "_localized_titles", load_titles)
    monkeypatch.setattr(story_map, "_first_observed", load_first)
    batch = story_map._SnapshotBatch(records, {10: "2.1.0"}, {
        key: [((2, 0, 0), 1, "old"), ((2, 1, 0), 2, value), ((3, 0, 0), 3, "future")]
        for key, value in titles.items()
    })
    ordinary = await story_map._snapshot_map(None, release, "en", 1, ["2.1.0"])
    batched = await story_map._snapshot_map(None, release, "en", 1, ["2.1.0"], batch)
    assert batched == ordinary
    assert batch.first_for(SimpleNamespace(game_version="2.0.0")) == {}


@pytest.mark.asyncio
async def test_batch_loads_all_versions_once_and_filters_future_titles(monkeypatch):
    releases = [SimpleNamespace(id=1, game_version="1.0.0", sequence=1),
                SimpleNamespace(id=2, game_version="2.0.0", sequence=2)]
    records = [
        (1, story_map.QUEST_PATH, SimpleNamespace(data={"QuestId": 10, "Data": {"TidName": "shared"}})),
        (2, story_map.TREE_NODE_PATH, SimpleNamespace(data={"Name": "node", "QuestArray": [10, 11]})),
    ]
    title_rows = [("shared", "Old title", "1.0.0", 1),
                  ("shared", "New title", "2.0.0", 2),
                  ("node", "Node", "2.0.0", 2)]

    class Session:
        calls = 0

        async def execute(self, _query):
            self.calls += 1
            return records if self.calls == 1 else title_rows

    async def first(_session, release, ids):
        assert release is releases[-1]
        assert ids == {10, 11}
        return {10: "1.0.0", 11: "2.0.0"}

    monkeypatch.setattr(story_map, "_first_observed", first)
    session = Session()
    batch = await story_map._snapshot_batch(session, releases, "en")
    assert session.calls == 2
    assert batch.titles_for(releases[0]) == {"shared": "Old title"}
    assert batch.titles_for(releases[1]) == {"shared": "New title", "node": "Node"}
    assert batch.first_for(releases[0]) == {10: "1.0.0"}
    assert batch.first_for(releases[1]) == {10: "1.0.0", 11: "2.0.0"}
