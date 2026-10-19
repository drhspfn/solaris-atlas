from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.db.repositories.nodes import get_node, get_quest
from wuwa_story.ingestion.slug_backfill import backfill, batch_sql
from wuwa_story.urls import node_address, node_url, record_url


def test_addresses_preserve_identity_and_use_full_stored_slug():
    node = SimpleNamespace(id=9223372036854775806, canonical_key="item:3", slug="9223372036854775806-astrite")
    assert node_url(node, "item") == "/items/9223372036854775806-astrite"
    assert node.canonical_key == "item:3"
    node.slug = None
    assert node_address(node, "item") == {"slug": None, "href": "/items/item%3A3"}
    assert node_url(SimpleNamespace(canonical_key="quest:42", slug=None), "quest") == "/quests/42"
    assert record_url(SimpleNamespace(id=4, slug="4-matrix"), "events") == "/events?event=4-matrix"


@pytest.mark.asyncio
async def test_slug_resolution_uses_exact_value_and_type_scope():
    session = AsyncMock()
    node = SimpleNamespace(id=12)
    session.scalars.return_value = [node]
    assert await get_node(session, "12-astrite", ("item",)) is node
    statement = session.scalars.call_args.args[0]
    assert "graph.node.slug" in str(statement)
    assert "node_type.key" in str(statement)
    assert "12-astrite" in statement.compile().params.values()
    session.scalars.return_value = []
    assert await get_node(session, "12-wrong-name", ("item",)) is None
    session.scalars.return_value = [node, SimpleNamespace(id=13)]
    assert await get_node(session, "ambiguous") is None


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
