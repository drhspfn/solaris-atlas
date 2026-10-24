from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.quest_classification import (
    QUEST_DATA,
    QUEST_MAIN_TYPE,
    QUEST_TYPE,
    classify_quest,
    item_requirement,
    quest_classification,
    quest_item_requirements,
)

TEST_TYPE = {"Id": 100, "MainId": 100, "IsShowInQuestPanel": False}
TEST_MAIN = {"Id": 100, "QuestTabIcon": "/Game/UI/SP_QuestPagingTest.SP_QuestPagingTest"}


def test_emerald_test_uses_game_type_evidence_and_never_becomes_a_reward():
    result = classify_quest({"Key": "测试21020015武器", "Type": 100}, TEST_TYPE, TEST_MAIN)
    assert result["kind"] == "test"
    assert result["visible_in_quest_panel"] is False
    assert result["evidence"][0]["field"] == "QuestMainType.QuestTabIcon"
    assert item_requirement(
        {"Condition": {"Type": "GetItem", "Items": [{"Count": 1, "ItemId": 21020015}]}}, 21020015
    ) == {
        "kind": "obtain_requirement",
        "label": "Requires obtaining this item",
        "count": 1,
        "field": "Data.Condition.Items",
        "is_reward": False,
    }


def test_type_does_not_require_test_word_and_gallery_keeps_separate_role():
    assert classify_quest({"Key": "任务4", "Type": 100}, TEST_TYPE, TEST_MAIN)["kind"] == "test"
    assert (
        classify_quest({"Key": "1.1主线_剧情图鉴专用", "Type": 1}, {}, {})["kind"]
        == "story_gallery"
    )


def test_older_snapshots_with_incomplete_type_dictionaries_keep_explicit_fixture_names():
    for key in ("测试21020015武器", "测试42100020物品", "测试60000023声骸", "test/护送Npc"):
        result = classify_quest({"Key": key}, {}, {})
        assert result["kind"] == "test"
        assert result["evidence"] == [{"field": "Data.Key", "value": key}]
        assert result["visible_in_quest_panel"] is None


def test_missing_dialogue_or_localization_and_ordinary_names_are_not_test_signals():
    for data in ({}, {"Key": "Contest"}, {"Key": "废弃工厂调查"}, {"Key": "测试勇气", "Type": 2}):
        assert classify_quest(data, {"IsShowInQuestPanel": True}, {})["kind"] == "unclassified"
    assert classify_quest({"Type": 100}, {}, {})["kind"] == "unclassified"
    assert (
        classify_quest({"Key": "Hidden follow-up"}, {"IsShowInQuestPanel": False}, {})["kind"]
        == "internal"
    )
    assert classify_quest({"Key": "【废弃】old quest"}, {}, {})["kind"] == "deprecated"


def test_exact_item_and_condition_type_are_required():
    for condition in (
        {"Type": "GetItem", "Items": [{"ItemId": 210200150}]},
        {"Type": "GetItem", "Items": [{"ItemId": "21020015"}]},
        {"Type": "Kill", "Items": [{"ItemId": 21020015}]},
    ):
        assert item_requirement({"Condition": condition}, 21020015)["kind"] == "reference"


def test_hand_in_both_authored_formats_without_matching_unrelated_ids():
    for hand_in in (
        {"Items": [{"Count": 2, "ItemIds": [7, 8]}]},
        {"GroupConfig": {"Count": 2, "ItemIds": [7]}},
    ):
        result = item_requirement({"Condition": {"Type": "HandInItems", "HandInItems": hand_in}}, 7)
        assert (result["kind"], result["count"], result["is_reward"]) == (
            "hand_in_requirement",
            2,
            False,
        )
        assert (
            item_requirement({"Condition": {"Type": "HandInItems", "HandInItems": hand_in}}, 70)[
                "kind"
            ]
            == "reference"
        )


def test_requirement_collection_keeps_hand_in_choices_and_never_scans_arbitrary_item_ids():
    result = quest_item_requirements(
        {
            "Condition": {
                "Type": "HandInItems",
                "HandInItems": {"Items": [{"ItemIds": [7, 8], "Count": 1}]},
            }
        }
    )
    assert [item["item_id"] for item in result] == [7, 8]
    assert all(item["alternatives"] and item["is_reward"] is False for item in result)
    assert quest_item_requirements({"Type": "GiveItem", "ItemId": 7}) == []


@pytest.mark.asyncio
async def test_classification_reads_selected_snapshot_and_reuses_only_its_type_dictionary():
    row = SimpleNamespace(
        row_index=253, data={"QuestId": 139000037, "Data": {"Type": 100, "Key": "测试21020015武器"}}
    )
    session = SimpleNamespace(
        info={},
        execute=AsyncMock(
            side_effect=[
                [(row, QUEST_DATA)],
                [
                    (SimpleNamespace(data=TEST_TYPE, row_index=7), QUEST_TYPE),
                    (SimpleNamespace(data=TEST_MAIN, row_index=7), QUEST_MAIN_TYPE),
                ],
                [(row, QUEST_DATA)],
                [],
            ]
        ),
    )
    result = await quest_classification(session, 139000037, 5)
    assert result["kind"] == "test"
    assert result["source"] == {"file": QUEST_DATA, "row": 253, "release_id": 5}
    assert await quest_classification(session, 139000037, 5) == result
    assert session.execute.await_count == 3
    assert (await quest_classification(session, 139000037, 4))["kind"] == "unclassified"
    statement = session.execute.call_args_list[0].args[0]
    assert 5 in statement.compile().params.values()
    assert 139000037 in statement.compile().params.values()
