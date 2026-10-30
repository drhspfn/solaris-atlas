"""Search words and filters come from localized public text and exact source paths."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.read_models import source_payloads as payloads
from wuwa_story.read_models import source_relations as relations


@pytest.mark.parametrize(
    "kind,payload,expected",
    [
        (
            "character",
            {
                "archive": {
                    "biography": "人物の経歴",
                    "skills": [{"name": "技能", "description": "技能の説明"}],
                    "voice_lines": [{"title": "挨拶", "text": "こんにちは"}],
                }
            },
            ["人物の経歴", "技能の説明", "こんにちは"],
        ),
        (
            "item",
            {
                "profile": {
                    "item": {"description": {"content": "<color=red>本文 &amp; 説明</color>"}}
                }
            },
            ["本文 & 説明"],
        ),
        ("quest", {"transcript": {"lines": [{"text": {"content": "物語の台詞"}}]}}, ["物語の台詞"]),
        (
            "dialogue",
            {
                "dialogue": {"text": {"content": "本人の台詞"}},
                "context": [{"text": {"content": "OtherSpeakerContext"}}],
            },
            ["本人の台詞"],
        ),
        (
            "game_notice",
            {
                "blocks": [
                    {"type": "text", "text": "お知らせの本文"},
                    {"type": "image", "url": "https://cdn/NotText"},
                ]
            },
            ["お知らせの本文"],
        ),
        (
            "game_event",
            {
                "event": {"description": "イベントの説明"},
                "occurrences": [{"title": "開催", "description": "期間限定"}],
                "unversioned_occurrences": [{"description": "UnknownVersionWords"}],
            },
            ["イベントの説明", "期間限定"],
        ),
        ("cutscene", {"title": "場面の題名", "description": "場面の説明"}, ["場面の説明"]),
    ],
)
def test_search_text_indexes_only_displayed_entity_words_not_raw_paths_or_other_context(
    kind, payload, expected
):
    payload["node"] = {"metadata_json": {"text": "RawConfigWords", "path": "/Game/RawAsset"}}
    payload["source_record"] = {"raw_record": {"Content": "RawSourceWords"}}
    payload["admin_actions"] = {"description": "PrivateWords"}
    result = payloads._searchable_text(kind, "公開の題名", {"aliases": ["公開の別名"]}, payload)
    assert all(text in result for text in ["公開の題名", "公開の別名", *expected])
    assert not any(
        text in result
        for text in [
            "RawConfigWords",
            "RawSourceWords",
            "RawAsset",
            "OtherSpeakerContext",
            "PrivateWords",
            "UnknownVersionWords",
            "NotText",
        ]
    )
    assert "<color" not in result


def test_historical_search_cannot_index_future_localization_even_if_source_response_is_wrong():
    payload = {"dialogue": {"text": {"content": "Future words", "source_game_version": "3.11.0"}}}
    with pytest.raises(ValueError, match="future localization"):
        payloads._searchable_text("dialogue", "Title", {}, payload, "3.10.0")


@pytest.mark.asyncio
async def test_dialogue_search_filters_use_exact_release_crosswalks_and_game_quest_ids():
    session = Row(
        execute=AsyncMock(
            side_effect=[
                Row(one_or_none=lambda: (4, 9)),
                [("character:70", "7-character", 7), ("character:70", "7-character", 7)],
            ]
        ),
        scalars=AsyncMock(return_value=[700, 701, 700]),
    )
    result = await relations._dialogue_filters(session, 101, 12)
    assert result == {"character_keys": ["character:70"], "quest_ids": [700, 701]}
    queries = [
        str(
            call.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        for call in session.execute.await_args_list
    ]
    assert "source_record.release_id = 12" in queries[0]
    assert "references_character" in queries[1] and "edge.basis = 'exact_join'" in queries[1]
    assert "edge_evidence.release_id = 12" in queries[1] and "JOIN core.character" in queries[1]
    quest_sql = str(
        session.scalars.await_args.args[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "core.quest.game_quest_id" in quest_sql
    assert "edge_evidence.release_id = 12" in quest_sql


@pytest.mark.asyncio
async def test_unproven_dialogue_snapshot_has_no_character_or_quest_filters():
    session = Row(
        execute=AsyncMock(return_value=Row(one_or_none=lambda: None)), scalars=AsyncMock()
    )
    assert await relations._dialogue_filters(session, 101, 12) == {
        "character_keys": [],
        "quest_ids": [],
    }
    session.execute.assert_awaited_once()
    session.scalars.assert_not_awaited()
