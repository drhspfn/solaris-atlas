import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from wuwa_story.api.public_addresses import attach_addresses
from wuwa_story.db.repositories.nodes import get_node, get_quest
from wuwa_story.ingestion.slug_backfill import backfill, batch_sql
from wuwa_story.urls import node_address, node_url, numeric_id, record_url


def test_addresses_preserve_identity_and_use_full_stored_slug():
    node = SimpleNamespace(id=9223372036854775806, canonical_key="item:3", slug="9223372036854775806-astrite")
    assert node_url(node, "item") == "/items/9223372036854775806-astrite"
    assert node.canonical_key == "item:3"
    node.slug = None
    assert node_address(node, "item") == {"slug": None, "href": "/items/item%3A3"}
    assert node_url(SimpleNamespace(canonical_key="quest:42", slug=None), "quest") == "/quests/42"
    assert record_url(SimpleNamespace(id=4, slug="4-matrix"), "events") == "/events?event=4-matrix"


def test_url_numbers_cannot_overflow_database_ids():
    assert numeric_id("3") == 3
    assert numeric_id("9223372036854775807") == 9223372036854775807
    assert numeric_id("9223372036854775808") is None
    assert numeric_id("²") is None
    assert numeric_id("3-astrite") is None


@pytest.mark.asyncio
async def test_slug_resolution_uses_exact_value_and_type_scope():
    session = AsyncMock()
    node = SimpleNamespace(id=12, canonical_key="item:3")
    session.scalars.return_value = [node]
    assert await get_node(session, "12-astrite", ("item",)) is node
    statement = session.scalars.call_args.args[0]
    assert "graph.node.slug" in str(statement)
    assert "node_type.key" in str(statement)
    assert "12-astrite" in statement.compile().params.values()
    session.scalars.return_value = []
    assert await get_node(session, "12-wrong-name", ("item",)) is None
    session.scalars.return_value = [node, SimpleNamespace(id=13)]
    assert await get_node(session, "1-ambiguous") is None


@pytest.mark.asyncio
async def test_quests_keep_legacy_game_ids():
    session = AsyncMock()
    quest = SimpleNamespace(game_quest_id=3)
    session.scalar.return_value = quest
    assert await get_quest(session, "3") is quest
    assert 3 in session.scalar.call_args.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_backfill_validates_limits_before_connecting():
    with pytest.raises(ValueError):
        await backfill(workers=50)
    with pytest.raises(ValueError):
        await backfill(batch_size=0)
    for group in ("nodes", "events", "notices"):
        sql = batch_sql(group)
        assert "SKIP LOCKED" in sql and "slug IS NULL" in sql and ":batch" in sql


@pytest.mark.asyncio
async def test_nested_addresses_are_batched_and_source_data_is_not_rewritten():
    session = AsyncMock()
    item = SimpleNamespace(id=10, canonical_key="item:3", slug="10-astrite")
    quest = SimpleNamespace(id=11, canonical_key="quest:8", slug="11-the-arrival")
    session.execute.side_effect = [[(item, "item"), (quest, "quest")], [(8, quest)]]
    raw = {"canonical_key": "item:999", "href": "source-data"}
    payload = {"results": [{"canonical_key": "item:3"}] * 10,
               "quest": {"game_quest_id": 8}, "neighbor": {"quest_ids": [8]},
               "prerequisite": {"quest": "quest:8"}, "source": raw}
    result = await attach_addresses(session, payload)
    assert session.execute.await_count == 2
    assert result["results"][0]["href"] == "/items/10-astrite"
    assert result["quest"]["game_quest_id"] == 8
    assert result["quest"]["href"] == "/quests/11-the-arrival"
    assert result["neighbor"]["quests"][0]["slug"] == "11-the-arrival"
    assert result["prerequisite"]["href"] == "/quests/11-the-arrival"
    assert raw == {"canonical_key": "item:999", "href": "source-data"}


@pytest.mark.asyncio
async def test_dialogue_search_accepts_the_character_slug(monkeypatch):
    from wuwa_story.api.routes.story import transcripts

    session = AsyncMock()
    session.scalars.return_value = []
    session.scalar.return_value = SimpleNamespace(id=1)
    result = MagicMock()
    result.all.return_value = []
    session.execute.return_value = result
    lookup = AsyncMock(return_value=SimpleNamespace(id=12))
    monkeypatch.setattr(transcripts, "get_node", lookup)
    payload = await transcripts.search_dialogue(q="hello", character="12-sanhua", quest_id=None,
        locale="en", game_version=None, limit=10, offset=0, session=session)
    assert payload["results"] == []
    lookup.assert_awaited_once_with(session, "12-sanhua", ("character",))


@pytest.mark.asyncio
async def test_character_archive_uses_game_role_identity_after_slug_resolution(monkeypatch):
    module = importlib.import_module("wuwa_story.api.routes.story.character_archive")
    session = AsyncMock()
    session.get.return_value = SimpleNamespace()
    session.execute.return_value = []
    monkeypatch.setattr(module, "get_node", AsyncMock(return_value=SimpleNamespace(id=12, canonical_key="character:1102")))
    monkeypatch.setattr(module, "_release_id", AsyncMock(return_value=1))
    monkeypatch.setattr(module, "_localized_strings", AsyncMock(return_value={}))
    monkeypatch.setattr(module, "S3Storage", lambda _: SimpleNamespace())

    async def records(_session, _release, path, _field, value):
        if path == "BinData/role/roleinfo.json":
            assert value == 1102
            return [SimpleNamespace(id=1, row_index=0, data={"Id": 1102})]
        return []

    monkeypatch.setattr(module, "_records", records)
    result = await module.character_archive("12-sanhua", locale="en", game_version=None, session=session)
    assert result["canonical_key"] == "character:1102"


@pytest.mark.asyncio
async def test_backfill_retries_transient_lock_contention(monkeypatch):
    from sqlalchemy.exc import DBAPIError

    from wuwa_story.ingestion import slug_backfill

    class Busy(Exception):
        sqlstate = "55P03"

    session = AsyncMock()
    session.scalars.side_effect = [DBAPIError("batch", {}, Busy()), [1], [], [], []]
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(slug_backfill, "SessionFactory", MagicMock(return_value=context))
    sleep = AsyncMock()
    monkeypatch.setattr(slug_backfill.asyncio, "sleep", sleep)
    result = await slug_backfill.backfill(workers=1, batch_size=1, pause=0)
    assert result == {"nodes": 1, "events": 0, "notices": 0}
    assert session.commit.await_count == 4
    assert sleep.await_args_list[0].args == (0.5,)
