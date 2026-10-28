from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from wuwa_story.api.pagination import cursor_page, cursor_scope, decode_cursor, encode_cursor
from wuwa_story.api.routes.cutscenes import catalog
from wuwa_story.ingestion.cutscene_order import quest_order


def test_story_order_uses_chapters_and_authored_links_not_quest_ids():
    quests = [{"QuestId": 20, "Data": {"ChapterId": 1, "ProvideType": {"Conditions": [{"Type": "PreQuest", "PreQuest": 90}]}}},
              {"QuestId": 90, "Data": {"ChapterId": 1}},
              {"QuestId": 10, "Data": {"ChapterId": 2}},
              {"QuestId": 1, "Data": {}}]
    assert quest_order(quests, []) == {90: 0, 20: 1, 10: 2}
    tree = [{"Id": 8, "ChapterId": 1, "PreNode": [9], "QuestArray": [20]},
            {"Id": 9, "ChapterId": 1, "QuestArray": [90]},
            {"Id": 3, "ChapterId": 2, "QuestArray": [10]}]
    assert quest_order(quests, tree) == {90: 0, 20: 1, 10: 2}


def test_cursor_is_filter_scoped_and_rejects_malformed_values():
    scope = cursor_scope("scenes", "1.0.0", True)
    token = encode_cursor([1, "Start"], scope)
    assert decode_cursor(token, scope, (int, str)) == [1, "Start"]
    for invalid in ("not base64!", encode_cursor([True, "Start"], scope)):
        with pytest.raises(ValueError):
            decode_cursor(invalid, scope, (int, str))
    with pytest.raises(ValueError):
        decode_cursor(token, cursor_scope("scenes", "2.0.0", True), (int, str))
    rows, next_cursor = cursor_page([1, 2, 3], 2, scope, lambda item: [item])
    assert rows == [1, 2]
    assert decode_cursor(next_cursor, scope, (int,)) == [2]


@pytest.mark.asyncio
async def test_catalog_rejects_cursor_when_order_has_been_rebuilt():
    session = AsyncMock()
    session.scalar.return_value = 2
    scope = cursor_scope("cutscenes", None, "", True)
    token = encode_cursor([1, 1, 0, 0, 0, "Start", 42], scope)
    with pytest.raises(HTTPException) as error:
        await catalog(game_version=None, locale="en", q="", playable=True,
                      limit=24, offset=0, cursor=token, session=session)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "cursor_expired"
