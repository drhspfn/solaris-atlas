"""Flagged public routes cannot fall through to source DB work or address hydration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

import test_browse_read_models
from wuwa_story.api import read_projections as reader
from wuwa_story.api.routes import cutscenes, game_events, nodes, notices, search
from wuwa_story.api.routes.story import (
    catalog,
    character_archive,
    continuity,
    map,
    media,
    profiles,
    transcripts,
)
from wuwa_story.read_models import context
from wuwa_story.read_models.readers import entities

database = test_browse_read_models.database

ROUTES = (
    ("/catalog?category=character", "catalog_page"),
    ("/categories", "category_counts"),
    ("/characters/character:7/profile", "profile_page"),
    ("/items/item:7/profile", "profile_page"),
    ("/locations/area:7/profile", "profile_page"),
    ("/quests/7/profile", "profile_page"),
    ("/characters/character:7/archive", "archive_page"),
    ("/nodes/speaker:7", "node_section"),
    ("/nodes/speaker:7/related", "node_related_page"),
    ("/nodes/speaker:7/edges", "node_section"),
    ("/nodes/speaker:7/source-record", "node_section"),
    ("/nodes/talk_item:7/narrative-context", "node_section"),
    ("/search?q=Name", "search_page"),
    ("/events?server=asia&summary=true", "events_page"),
    ("/events/7/detail?server=asia&occurrence_id=9", "event_detail"),
    ("/notices", "notices_page"),
    ("/notices/7", "notice_detail_payload"),
    ("/cutscenes", "cutscene_catalog"),
    ("/cutscenes/Scene", "cutscene_detail"),
    ("/story-map", "story_map_page"),
    ("/dialogue/search?q=Name", "dialogue_search_page"),
    ("/quests/7/transcript?include_raw=false", "transcript_page"),
    ("/quests/7/continuity", "quest_section"),
    ("/quests/7/media", "quest_section"),
)


@pytest.fixture(autouse=True)
def coverage(monkeypatch):
    monkeypatch.setattr(
        entities,
        "_coverage",
        AsyncMock(return_value=SimpleNamespace(published_generation=1, status="pending")),
    )


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(
        context, "get_settings", lambda: SimpleNamespace(read_projections_enabled=True)
    )
    application = FastAPI()
    for module in (
        catalog,
        profiles,
        character_archive,
        nodes,
        search,
        game_events,
        notices,
        cutscenes,
        map,
        transcripts,
        continuity,
        media,
    ):
        application.include_router(module.router)
        # There are deliberately no query methods. Any source read or decorator
        # hydration fails this test instead of silently using a real connection.
        application.dependency_overrides[module.get_session] = lambda: SimpleNamespace()
    return application


@pytest.mark.asyncio
@pytest.mark.parametrize("path,helper", ROUTES)
async def test_enabled_route_returns_projection_without_any_source_read(
    app, monkeypatch, path, helper
):
    body = reader.ProjectedPayload({"projected": True})
    if path == "/nodes/speaker:7":
        body = {
            "id": 7,
            "canonical_key": "speaker:7",
            "slug": None,
            "type_id": 1,
            "status": "active",
            "metadata_json": {},
        }
    if path.endswith("/edges"):
        body = []
    projected = AsyncMock(return_value=body)
    monkeypatch.setattr(reader, helper, projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 200, response.text
    assert response.json() == body
    projected.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,helper", ROUTES)
async def test_unready_projection_never_falls_back_to_source(app, monkeypatch, path, helper):
    projected = AsyncMock(side_effect=reader._unready())
    monkeypatch.setattr(reader, helper, projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    projected.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_flag_and_nested_source_build_preserve_source_builder(app, monkeypatch):
    node = AsyncMock(return_value=None)
    projected = AsyncMock(return_value=reader.ProjectedPayload({"projected": True}))
    monkeypatch.setattr(profiles, "get_node", node)
    monkeypatch.setattr(reader, "profile_page", projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        with context.source_build():
            assert not reader.should_route_to_projection()
            with context.source_build():
                assert (await client.get("/characters/character:7/profile")).status_code == 404
            assert not reader.should_route_to_projection()
        assert reader.should_route_to_projection()
        assert (await client.get("/characters/character:7/profile")).json() == {"projected": True}
        monkeypatch.setattr(
            context, "get_settings", lambda: SimpleNamespace(read_projections_enabled=False)
        )
        assert (await client.get("/characters/character:7/profile")).status_code == 404
    assert node.await_count == 2
    projected.assert_awaited_once()


@pytest.mark.asyncio
async def test_catalog_sql_reads_physical_card_and_typed_filters_not_large_payload(
    database, monkeypatch
):
    monkeypatch.setattr(entities, "_locale_id", AsyncMock(return_value=1))
    database.entry(
        "character",
        1,
        payload={"archive": {"text": "x" * 130000}},
        card={"node_type": "character", "href": "/characters/1", "aliases": []},
        role_type=1,
    )
    result = await reader.catalog_page(database, "character")
    assert result["results"][0]["id"] == 1
    for statement in database.statements:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert ".payload" not in sql and ".artwork" not in sql
    assert "character_read.role_type" in str(database.statements[0])
    page = database.statements[-1]
    assert "card" in page.selected_columns.keys()
    assert "payload" not in page.selected_columns.keys()


@pytest.mark.asyncio
async def test_page_sql_selects_only_requested_section(database, monkeypatch):
    monkeypatch.setattr(entities, "_locale_id", AsyncMock(return_value=1))
    database.entry(
        "character", 1, payload={"profile": {"name": "Short"}, "archive": {"text": "x" * 130000}}
    )
    assert await reader.page_payload(database, "character", 1, selector="profile") == {
        "name": "Short"
    }
    statement = database.statements[-1]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "profile" in compiled.params.values() and "archive" not in compiled.params.values()
    assert "character_read.payload[" in sql
    assert "character_read.payload," not in sql
    assert "artwork" not in statement.selected_columns.keys()


@pytest.mark.asyncio
async def test_repository_browse_is_also_lightweight(database):
    from wuwa_story.db.repositories.read_models import browse_entries

    database.entry("quest", 1, payload={"transcript": {"lines": ["x" * 130000]}})
    total, entries = await browse_entries(database, "quest", 1)
    assert total == 1 and entries[0].card["node_type"] == "quest"
    sql = str(database.statements[-1].compile(dialect=postgresql.dialect()))
    assert ".payload" not in sql and ".artwork" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key", ["quest_action:7", "quest_state:7", "flow:7", "source_reference:7", "7-technical-slug"]
)
async def test_technical_node_explorer_routing_is_explicit_and_not_a_projection_miss(
    app, monkeypatch, key
):
    assert not reader.supports_node_projection(key)
    node = SimpleNamespace(
        id=7, canonical_key=key, slug=None, type_id=99, status="active", metadata_json={}
    )
    lookup = AsyncMock(return_value=node)
    monkeypatch.setattr(nodes, "get_node", lookup)
    projected = AsyncMock(
        side_effect=AssertionError("Technical inspection must not enter projected lookup")
    )
    monkeypatch.setattr(reader, "node_section", projected)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/nodes/{key}")
    assert response.status_code == 200 and response.json()["canonical_key"] == key
    lookup.assert_awaited_once()
    projected.assert_not_awaited()


@pytest.mark.asyncio
async def test_phone_message_is_projected_and_missing_supported_entry_never_uses_source(
    app, monkeypatch
):
    assert reader.supports_node_projection("phone_message:7")
    projected = AsyncMock(side_effect=reader._unready())
    lookup = AsyncMock(side_effect=AssertionError("No source fallback on a supported-kind miss"))
    monkeypatch.setattr(reader, "node_section", projected)
    monkeypatch.setattr(nodes, "get_node", lookup)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/nodes/phone_message:7")
    assert response.status_code == 503
    projected.assert_awaited_once()
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_entity_tool_uses_projected_search_without_legacy_graph_queries(
    app, monkeypatch
):
    from starlette.requests import Request

    from wuwa_story.agents import providers, settings
    from wuwa_story.api.routes.story import shared
    from wuwa_story.db.repositories import search as legacy

    class Provider:
        def __init__(self, *_):
            self.turn = 0

        def request(self, *_):
            return "/unused", {}

        async def post(self, *_):
            self.turn += 1
            return self.turn

        def parse(self, turn):
            return SimpleNamespace(
                calls=[
                    SimpleNamespace(
                        name="search_entities", arguments={"query": "Story", "category": "quest"}
                    )
                ]
                if turn == 1
                else [],
                items=[],
                text="done",
            )

        def tool_result(self, _, value):
            return {"result": value}

    monkeypatch.setattr(providers, "Provider", Provider)
    monkeypatch.setattr(settings, "get_agent_settings", lambda: None)
    projected = AsyncMock(
        return_value={
            "results": [{"title": "Story", "node_type": "quest", "canonical_key": "quest:7"}]
        }
    )
    legacy_search = AsyncMock(side_effect=AssertionError("Legacy source search is forbidden"))
    label = AsyncMock(side_effect=AssertionError("Source labels are forbidden"))
    monkeypatch.setattr(reader, "search_page", projected)
    monkeypatch.setattr(legacy, "lexical_search", legacy_search)
    monkeypatch.setattr(shared, "_node_label", label)
    response = await search.public_chat(
        Request({"type": "http", "client": None}),
        search.PublicChatRequest(messages=[]),
        session=SimpleNamespace(),
    )
    assert response == {"reply": "done"}
    projected.assert_awaited_once()
    legacy_search.assert_not_awaited()
    label.assert_not_awaited()
