"""Staged HTTP projection contracts, with no source graph available in the database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

import test_browse_read_models
from wuwa_story.api import read_projections as reader
from wuwa_story.read_models.readers import entities

database = test_browse_read_models.database


@pytest.fixture
def projections(database, monkeypatch):
    monkeypatch.setattr(entities, "_locale_id", AsyncMock(return_value=1))
    monkeypatch.setattr(
        entities,
        "_coverage",
        AsyncMock(return_value=SimpleNamespace(published_generation=1, status="pending")),
    )
    return database


def payload(kind="location", **filters):
    return {
        "card": {"node_type": kind, "aliases": ["Alias"], "href": "/locations/7-place", **filters},
        "profile": {"location": {"name": "Complete public profile"}},
        "archive": {"quests": [], "speakers": []},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [7, "7", "7-place", "area:7"])
async def test_projection_resolves_exact_id_slug_canonical_key_without_source_joins(
    projections, identity
):
    projections.entry(
        "location", 7, canonical_key="area:7", slug="7-place", payload=payload("area")
    )
    entry = await reader.projection_entry(projections, "area", identity)
    assert entry.entity_id == 7 and entry.canonical_key == "area:7"
    sql = str(projections.statements[-1].compile(dialect=postgresql.dialect()))
    assert "FROM core.location_read" in sql and "JOIN" not in sql
    assert "locale_id =" in sql and "snapshot_key =" in sql


@pytest.mark.asyncio
async def test_page_selectors_and_exact_snapshot_do_not_fall_back(projections):
    current = payload()
    historical = {"profile": {"location": {"name": "Historical"}}}
    projections.entry("location", 7, payload=current)
    projections.entry("location", 7, snapshot_key="3.10", payload=historical)
    assert await reader.page_payload(projections, "location", 7) == current["profile"]
    assert (
        await reader.page_payload(projections, "location", 7, selector="archive")
        == current["archive"]
    )
    assert (
        await reader.page_payload(projections, "location", 7, snapshot_key="3.10")
        == historical["profile"]
    )
    for options in ({"snapshot_key": "3.9"}, {"snapshot_key": "3.10", "selector": "archive"}):
        with pytest.raises(HTTPException) as error:
            await reader.page_payload(projections, "location", 7, **options)
        assert error.value.status_code == 503
        assert error.value.headers == {"Retry-After": "2", "Cache-Control": "no-store"}
    entities._locale_id.return_value = 2
    with pytest.raises(HTTPException) as error:
        await reader.page_payload(projections, "location", 7)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_event_and_notice_can_return_complete_stored_payload(projections):
    for alias, kind in (("event", "game_event"), ("notice", "game_notice")):
        stored = {kind: {"id": 7, "title": "Published"}, "card": {"aliases": []}}
        projections.entry(kind, 7, payload=stored)
        assert await reader.page_payload(projections, alias, 7, selector=None) == stored


@pytest.mark.asyncio
async def test_canonical_key_wins_and_ambiguous_or_partial_slug_is_unready(projections):
    projections.entry("location", 7, canonical_key="area:7", slug="same", payload=payload())
    projections.entry("location", 8, canonical_key="area:8", slug="area:7", payload=payload())
    projections.entry("location", 9, canonical_key="area:9", slug="same", payload=payload())
    assert (await reader.projection_entry(projections, "location", "area:7")).entity_id == 7
    for identity in ("same", "7-missing", "unknown"):
        with pytest.raises(HTTPException) as error:
            await reader.projection_entry(projections, "location", identity)
        assert error.value.status_code == 503


def test_catalog_card_preserves_existing_shape_and_stored_image():
    entry = SimpleNamespace(
        entity_id=7,
        generation=12,
        card=payload("area")["card"],
        title="Published title",
        canonical_key="area:7",
        slug="7-place",
        primary_image_url="https://cdn.test/image",
    )
    assert reader.catalog_entry(entry, "area") == {
        "id": 7,
        "canonical_key": "area:7",
        "node_type": "area",
        "category": "location",
        "title": "Published title",
        "aliases": ["Alias"],
        "slug": "7-place",
        "href": "/locations/7-place",
        "image_url": "https://cdn.test/image",
    }
    for incomplete in (
        {},
        {"profile": {}},
        {"card": {"node_type": "area", "href": "/locations/7"}},
    ):
        entry.card = incomplete.get("card", {})
        with pytest.raises(HTTPException) as error:
            reader.catalog_entry(entry, "location")
        assert error.value.status_code == 503


@pytest.mark.parametrize(
    "entry",
    [
        None,
        SimpleNamespace(generation=0, payload={"profile": {"name": "Incomplete"}}),
        SimpleNamespace(generation=1, payload={}),
        SimpleNamespace(generation=1, payload=None),
    ],
)
def test_unpublished_generation_or_empty_payload_is_unready(entry):
    with pytest.raises(HTTPException) as error:
        reader._ready(entry)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_catalog_pagination_and_empty_unpublished_scope_are_distinct(projections):
    for identity, title in [(7, "Zulu"), (9, "alpha"), (8, "Alpha")]:
        projections.entry("location", identity, title=title, payload=payload("area"))
    result = await reader.catalog_page(projections, "area", limit=2)
    assert result["category"] == "location" and result["total"] == 3
    assert [row["id"] for row in result["results"]] == [8, 9]
    result = await reader.catalog_page(projections, "location", offset=100)
    assert result["total"] == 3 and result["results"] == []
    entities._coverage.return_value.published_generation = 0
    for category, snapshot in (("item", "current"), ("location", "3.9")):
        with pytest.raises(HTTPException) as error:
            await reader.catalog_page(projections, category, snapshot_key=snapshot)
        assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_catalog_filters_primary_roles_and_quest_types_using_only_payload(projections):
    for identity, role in [(1, 2), (2, 1), (3, None)]:
        projections.entry("character", identity, payload=payload("character", role_type=role))
    result = await reader.catalog_page(projections, "character")
    assert result["total"] == 2 and [row["id"] for row in result["results"]] == [2, 3]
    projections.entry("quest", 1, payload=payload("quest", quest_type=1))
    projections.entry(
        "quest", 2, payload={**payload("quest"), "profile": {"quest": {"quest_type": "3"}}}
    )
    result = await reader.catalog_page(projections, "quest", quest_type_id=3)
    assert result["total"] == 1 and [row["id"] for row in result["results"]] == [2]
    result = await reader.catalog_page(projections, "quest", quest_type_id=1)
    assert result["total"] == 1 and [row["id"] for row in result["results"]] == [1]
    result = await reader.catalog_page(projections, "quest", quest_type_id=9)
    assert result["total"] == 0 and result["results"] == []
    for statement in projections.statements:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "JOIN" not in sql and "graph." not in sql
        assert "core.character " not in sql and "core.quest " not in sql


@pytest.mark.asyncio
async def test_bad_kind_selector_and_pagination_are_rejected_without_query(projections):
    operations = [
        reader.page_payload(projections, "unknown", 1),
        reader.page_payload(projections, "location", 1, selector="unknown"),
        reader.catalog_page(projections, "unknown"),
        reader.catalog_page(projections, "location", quest_type_id=3),
        reader.catalog_page(projections, "location", limit=0),
        reader.catalog_page(projections, "location", offset=-1),
    ]
    for operation in operations:
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 400
    assert projections.statements == []


@pytest.mark.asyncio
async def test_locale_resolution_is_exact_dimension_lookup_and_unknown_is_400():
    session = SimpleNamespace(scalar=AsyncMock(return_value=5))
    assert await reader._locale_id(session, "en") == 5
    statement = session.scalar.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FROM i18n.locale" in sql and "JOIN" not in sql
    assert "en" in statement.compile().params.values()
    session.scalar.return_value = None
    with pytest.raises(HTTPException) as error:
        await reader._locale_id(session, "xx")
    assert error.value.status_code == 400
