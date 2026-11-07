from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from wuwa_story.api.pagination import cursor_page, cursor_scope, decode_cursor, encode_cursor
from wuwa_story.api.routes.cutscenes import catalog
from wuwa_story.ingestion.cutscene_order import quest_order


@pytest.mark.asyncio
async def test_catalog_uses_archive_sequence_for_nested_scenes_instead_of_source_row_order(monkeypatch):
    from wuwa_story.ingestion import cutscene_order as module
    from wuwa_story.story.review_order import SOURCE
    quest = SimpleNamespace(game_quest_id=100)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        [(module.QUESTS, {'QuestId': 100, 'Data': {'ChapterId': 1}}),
         (SOURCE, {'QuestId': 100, 'Data': [
             {'Flow': {'FlowListName': 'Story', 'FlowId': 1, 'StateId': 99}},
             {'Flow': {'FlowListName': 'Story', 'FlowId': 1, 'StateId': 1}}]})],
        [SimpleNamespace(node_id=5, cg_name='M0385'),
         SimpleNamespace(node_id=6, cg_name='M0390')],
        [(50, 500, 1, 'Story_1_99'), (60, 600, 1, 'Story_1_1')],
    ]))
    monkeypatch.setattr(module.cutscenes, 'owners', AsyncMock(
        return_value=[(5, quest, '', True), (6, quest, '', True)]))
    monkeypatch.setattr(module.cutscenes, 'source_parents',
                        AsyncMock(side_effect=[[(5, 50), (6, 60)], []]))
    result = await module.project(session, 21)
    assert [row['cutscene_node_id'] for row in result] == [5, 6]
    assert [row['sort_index'] for row in result] == [0, 1]


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
