"""Payload-shape adapters, publication availability and identity regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

import test_browse_read_models
from wuwa_story.api import read_projections as reader
from wuwa_story.read_models.readers import entities

database = test_browse_read_models.database


@pytest.fixture(autouse=True)
def published(monkeypatch):
    monkeypatch.setattr(entities, "_locale_id", AsyncMock(return_value=1))
    monkeypatch.setattr(
        entities,
        "_coverage",
        AsyncMock(
            return_value=SimpleNamespace(
                published_generation=1,
                generation=2,
                status="pending",
            )
        ),
    )


def result(rows):
    return Mock(mappings=Mock(return_value=Mock(all=Mock(return_value=rows))))


@pytest.mark.asyncio
async def test_published_pending_scope_stays_available_and_absence_waits_for_complete_coverage(
    database,
):
    database.entry("character", 7, payload={"profile": {"name": "Published"}})
    assert await reader.profile_page(database, "character", 7) == {"name": "Published"}
    with pytest.raises(HTTPException) as error:
        await reader.profile_page(database, "character", 8)
    assert error.value.status_code == 503
    entities._coverage.return_value.status = "ready"
    with pytest.raises(HTTPException) as error:
        await reader.profile_page(database, "character", 8)
    assert error.value.status_code == 404
    entities._coverage.return_value.published_generation = 0
    with pytest.raises(HTTPException) as error:
        await reader.profile_page(database, "character", 7)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_completed_empty_catalog_is_valid_but_initial_empty_scope_is_not(database):
    entities._coverage.return_value.status = "ready"
    assert (await reader.catalog_page(database, "item"))["results"] == []
    entities._coverage.return_value.published_generation = 0
    with pytest.raises(HTTPException) as error:
        await reader.catalog_page(database, "item")
    assert error.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["C0022", "M0366"])
async def test_cutscene_public_code_uses_exact_canonical_prefix(database, name):
    detail = {"name": name, "title": "Scene", "event": {}, "passages": []}
    database.entry(
        "cutscene",
        7,
        canonical_key=f"cutscene:{name}",
        payload={**detail, "catalog": {}, "node": {"id": 7}},
    )
    assert await reader.cutscene_detail(database, name) == detail


@pytest.mark.asyncio
async def test_quest_game_id_wins_over_projection_entity_id_collision(database):
    database.entry(
        "quest",
        7,
        canonical_key="quest:139000027",
        payload={"profile": {"quest": {"game_quest_id": 139000027}}},
    )
    database.entry(
        "quest",
        139000027,
        canonical_key="quest:999",
        payload={"profile": {"quest": {"game_quest_id": 999}}},
    )
    assert (await reader.profile_page(database, "quest", "139000027"))["quest"][
        "game_quest_id"
    ] == 139000027


@pytest.mark.asyncio
async def test_unicode_talk_item_key_is_not_decoded_or_reinterpreted(database):
    key = "talk_item:Ріна/夜:3:7"
    database.entry("dialogue", 7, canonical_key=key, payload={"dialogue": {"id": key}})
    assert (await reader.node_projection(database, key)).entity_id == 7


@pytest.mark.asyncio
async def test_story_map_exact_variant_snapshot_and_no_entity_search_membership(database):
    database.entry("story_map", 1, payload={"chapters": [{"id": "all"}]})
    database.entry("story_map", 4, snapshot_key="3.10", payload={"chapters": [{"id": "companion"}]})
    assert await reader.story_map_page(database) == {"chapters": [{"id": "all"}]}
    assert await reader.story_map_page(database, game_version="3.10", quest_type_id=3) == {
        "chapters": [{"id": "companion"}]
    }
    for invalid in (-1, True, "3"):
        with pytest.raises(HTTPException) as error:
            await reader.story_map_page(database, quest_type_id=invalid)
        assert error.value.status_code == 400
    with pytest.raises(HTTPException) as error:
        await reader.catalog_page(database, "story_map")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_cutscene_catalog_preserves_descending_order_filter_cursor_and_revision(database):
    for identity, version, playable in ((1, 6, True), (2, 7, True), (3, 9, False)):
        catalog = {
            "item": {"node_id": identity, "name": f"C{identity}", "title": f"Scene {identity}"},
            "cursor_values": [12, 3, version, 0, 4, f"C{identity}", identity],
            "playable": playable,
            "release_id": None,
            "imported_game_versions": [f"3.{version}.0"],
        }
        database.entry(
            "cutscene",
            identity,
            card={"catalog": catalog},
            payload={"playback_context": "x" * 200_000},
        )
    page = await reader.cutscene_catalog(database, limit=1)
    assert page["total"] == 2 and page["items"][0]["node_id"] == 2
    assert page["next_cursor"]
    following = await reader.cutscene_catalog(database, limit=1, cursor=page["next_cursor"])
    assert following["items"][0]["node_id"] == 1 and following["next_cursor"] is None
    assert (await reader.cutscene_catalog(database, playable=False))["total"] == 3
    assert "payload," not in str(database.statements[-1].compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_event_server_occurrence_version_and_summary_are_selected_from_captured_runs(
    database,
):
    def run(identity, server, version):
        return {
            "id": 7,
            "occurrence_id": identity,
            "server": server,
            "game_version": version,
            "title": f"Run {identity}",
            "starts_at": "2026-10-01T00:00:00Z",
            "artwork": [
                {"path": "preview", "role": "content"},
                {"path": "full", "role": "prefab_texture"},
            ],
        }

    database.entry(
        "game_event",
        7,
        payload={
            "event": {"id": 7, "kind": "limited"},
            "occurrences": [run(1, "asia", "3.10"), run(2, "europe", "3.9"), run(3, "asia", None)],
            "unversioned_occurrences": [],
        },
    )
    response = await reader.events_page(database, "asia", version="3.10", summary=True)
    assert {item["occurrence_id"] for item in response["events"]} == {1, 3}
    assert all(len(item["artwork"]) == 1 for item in response["events"])
    assert response["events"][0]["detail_url"].startswith("/events/7/detail?server=asia")
    assert (await reader.event_detail(database, 7, "europe", 2))["game_version"] == "3.9"
    with pytest.raises(HTTPException) as error:
        await reader.event_detail(database, 7, "asia", 2)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_node_sections_use_stored_raw_ids_and_direction_without_graph_read(
    database, monkeypatch
):
    from wuwa_story.read_models.readers import provenance

    monkeypatch.setattr(
        provenance,
        "source_record",
        AsyncMock(return_value={"source_record": {"raw_record": {"Id": 7}}}),
    )
    edges = [
        {"id": 1, "from_node_id": 7, "to_node_id": 8},
        {"id": 2, "from_node_id": 9, "to_node_id": 7},
    ]
    node = {"id": 7, "canonical_key": "speaker:7"}
    database.entry(
        "speaker",
        7,
        canonical_key="speaker:7",
        payload={
            "node": node,
            "edges": edges,
            "source_record": {"source_record": {"id": 70}},
        },
    )
    assert await reader.node_section(database, "speaker:7", "node") == node
    assert await reader.node_section(database, "speaker:7", "edges", direction="in") == [edges[1]]
    assert (await reader.node_section(database, "speaker:7", "source_record"))["source_record"][
        "raw_record"
    ] == {"Id": 7}
    assert all("graph." not in str(query) for query in database.statements)


@pytest.mark.asyncio
async def test_entity_search_is_union_of_versioned_light_projections_with_valid_filters(
    monkeypatch,
):
    monkeypatch.setattr(
        entities,
        "projection_entry",
        AsyncMock(return_value=SimpleNamespace(canonical_key="character:1")),
    )
    row = {
        "kind": "quest",
        "entity_id": 7,
        "locale_id": 1,
        "snapshot_key": "3.10",
        "title": "Story",
        "sort_title": "story",
        "canonical_key": "quest:139000027",
        "slug": "7-story",
        "primary_image_url": None,
        "primary_image_file_id": None,
        "generation": 1,
        "card": {"node_type": "quest", "href": "/quests/7-story", "aliases": []},
        "alias": "Story",
        "score": 1.0,
    }
    session = SimpleNamespace(execute=AsyncMock(return_value=result([row])))
    page = await reader.search_page(
        session,
        "Story",
        category=["quest"],
        game_version="3.10",
        character="character:1",
        quest_id=139000027,
    )
    assert page["results"][0]["id"] == 7 and page["snapshot_version"] == "3.10"
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "search.document" not in sql and "graph." not in sql and "JOIN" not in sql
    assert ".payload" not in sql and ".artwork" not in sql
    assert "quest_read.search_vector @@" in sql and "quest_read.card[" in sql
    assert "3.10" in compiled.params.values()


@pytest.mark.asyncio
async def test_dialogue_search_pages_small_authored_order_then_fetches_only_selected_line():
    line = {
        "id": "talk_item:7",
        "speaker": None,
        "text": {"content": "Exact line"},
        "game_ids": {},
        "source_type": "talk",
        "source_index": 2,
        "flow_state": "A",
        "action": None,
        "provenance": None,
        "player_choices": [],
        "raw_refs": {},
    }
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                result([{"entity_id": 7, "kind": "dialogue"}]),
                result([{"entity_id": 7, "line": line}]),
            ]
        )
    )
    page = await reader.dialogue_search_page(session, "Exact", game_version="3.10")
    assert (
        page["results"] == [{key: value for key, value in line.items() if key != "raw_refs"}]
        and page["game_version"] == "3.10"
    )
    first, second = [
        call.args[0].compile(dialect=postgresql.dialect())
        for call in session.execute.call_args_list
    ]
    assert ".payload" not in str(first) and "ORDER BY" in str(first) and "LIMIT" in str(first)
    assert "dialogue_read.payload[" in str(second)
    assert "search.document" not in str(first) and "JOIN" not in str(first)


@pytest.mark.asyncio
async def test_related_source_file_filters_known_mismatches_and_rejects_only_missing_associations(
    database,
):
    related = {
        "node": "speaker:7",
        "source_evidence": [],
        "results": [
            {
                "relation": "references",
                "direction": "outgoing",
                "node": {"id": 8, "type": "source_reference"},
                "provenance": {"source_file": "first.json"},
            },
            {
                "relation": "references",
                "direction": "incoming",
                "node": {"id": 9, "type": "source_reference"},
                "provenance": {"source_file": "second.json"},
            },
        ],
    }
    database.entry("speaker", 7, canonical_key="speaker:7", payload={"related": related})
    response = await reader.node_related_page(database, "speaker:7", source_file="second.json")
    assert [item["node"]["id"] for item in response["results"]] == [9]
    assert (await reader.node_related_page(database, "speaker:7", source_file="absent.json"))[
        "results"
    ] == []
    assert (
        await reader.node_related_page(
            database, "speaker:7", source_file="second.json", direction="out"
        )
    )["results"] == []
    with pytest.raises(HTTPException) as error:
        await reader.node_related_page(database, "speaker:7", direction="sideways")
    assert error.value.status_code == 422
    database.entry(
        "speaker",
        10,
        canonical_key="speaker:10",
        payload={
            "related": {
                "results": [
                    {"node": {"id": 11, "type": "source_reference"}, "provenance": {}},
                ]
            }
        },
    )
    with pytest.raises(HTTPException) as error:
        await reader.node_related_page(database, "speaker:10", source_file="first.json")
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_asia_only_event_keeps_authoritative_europe_outerjoin_fallback(database):
    undated = {
        "id": 7,
        "title": "Undated",
        "kind": "limited",
        "occurrence_id": None,
        "starts_at": None,
        "game_version": None,
        "artwork": [],
    }
    asia = {
        **undated,
        "title": "Asia run",
        "occurrence_id": 1,
        "server": "asia",
        "game_version": "3.10",
    }
    database.entry(
        "game_event",
        7,
        payload={"event": undated, "occurrences": [asia], "unversioned_occurrences": []},
    )
    europe = await reader.events_page(database, "europe", summary=True)
    assert len(europe["events"]) == 1 and europe["events"][0]["occurrence_id"] is None
    assert europe["events"][0]["title"] == "Undated"
    assert (await reader.event_detail(database, 7, "europe", None))["occurrence_id"] is None
    assert (await reader.events_page(database, "asia", version="3.11"))["events"] == []
    assert (await reader.events_page(database, "europe", version="3.11"))["events"][0][
        "occurrence_id"
    ] is None


@pytest.mark.asyncio
async def test_notice_revision_uses_precomputed_overrides_preserving_null_and_empty_without_source_reads(
    database,
):
    current, old = "a" * 64, "b" * 64
    stored = {
        "id": 7,
        "title": "Current",
        "revision": current,
        "revisions": [current, old],
        "banner_url": "https://cdn.test/current",
        "links": [{"url": "https://example.test"}],
        "blocks": [{"type": "text", "text": "unchanged"}],
        "revision_snapshot_format": "overrides-v1",
        "revision_snapshots": {
            old: {"title": "Old", "revision": old, "banner_url": None, "links": []}
        },
    }
    database.entry("game_notice", 7, payload=stored)
    result = await reader.notice_detail_payload(database, "7", old)
    assert result["title"] == "Old" and result["revision"] == old
    assert (
        result["banner_url"] is None
        and result["links"] == []
        and result["blocks"] == stored["blocks"]
    )
    assert not {"card", "revision_snapshots", "revision_snapshot_format"} & result.keys()
    assert all(
        "game_notice_revision" not in str(query) and "graph." not in str(query)
        for query in database.statements
    )
    entities._coverage.return_value.status = "ready"
    with pytest.raises(HTTPException) as error:
        await reader.notice_detail_payload(database, "7", "c" * 64)
    assert error.value.status_code == 404
    database.entry("game_notice", 8, payload={**stored, "id": 8, "revision_snapshots": {}})
    with pytest.raises(HTTPException) as error:
        await reader.notice_detail_payload(database, "8", old)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_uncategorized_historical_search_requests_only_supported_historical_kinds():
    session = SimpleNamespace(execute=AsyncMock(return_value=result([])))
    response = await reader.search_page(session, "Story", game_version="3.10")
    assert response["results"] == []
    requested = {call.args[1] for call in entities._coverage.call_args_list}
    assert requested == {"character", "item", "quest", "game_event", "cutscene"}
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert all(
        f"core.{kind}_read" not in sql
        for kind in ("location", "speaker", "game_notice", "story_map")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "term", ["123456789", "d41d8cd98f00b204e9800998ecf8427e", "こんにちは", "人物", "ALIAS"]
)
async def test_search_keeps_fulltext_and_substring_recall_for_numeric_hash_multilingual_terms(term):
    session = SimpleNamespace(execute=AsyncMock(return_value=result([])))
    response = await reader.search_page(session, term, category=["quest"], limit=24, offset=24)
    assert response["results"] == [] and response["offset"] == 24 and response["has_more"] is False
    query = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(query)
    assert "search_vector @@" in sql and "searchable_text ILIKE" in sql and " OR " in sql
    assert term in query.params.values() and ".payload" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "character",
        "item",
        "location",
        "quest",
        "game_event",
        "game_notice",
        "speaker",
        "dialogue",
        "cutscene",
    ],
)
async def test_all_searchable_categories_page_only_small_columns(kind):
    session = SimpleNamespace(execute=AsyncMock(return_value=result([])))
    scope = "dialogue" if kind == "dialogue" else "entities"
    response = await reader.search_page(
        session, "absent", category=[kind], scope=scope, limit=2, offset=2
    )
    assert response["results"] == [] and response["offset"] == 2
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert f"core.{kind}_read" in sql and "core.story_map_read" not in sql
    assert ".payload" not in sql and ".artwork" not in sql


@pytest.mark.asyncio
async def test_cutscene_catalog_sql_pages_physical_card_and_preserves_cursor_errors(database):
    from wuwa_story.api.pagination import cursor_scope, encode_cursor

    for identity in range(1, 5):
        database.entry(
            "cutscene",
            identity,
            card={
                "catalog": {
                    "item": {"node_id": identity, "name": f"C{identity}", "title": "Match"},
                    "cursor_values": [12, 3, 7, 0, identity, f"C{identity}", identity],
                    "playable": True,
                    "release_id": None,
                    "imported_game_versions": ["3.7"],
                }
            },
            payload={"catalog": {"item": {"title": "WRONG"}}, "body": "x" * 200_000},
        )
    first = await reader.cutscene_catalog(database, q="match", limit=1)
    second = await reader.cutscene_catalog(
        database, q="match", limit=1, cursor=first["next_cursor"]
    )
    assert [first["items"][0]["node_id"], second["items"][0]["node_id"]] == [4, 3]
    assert second["total"] == 4
    assert (await reader.cutscene_catalog(database, q="C2"))["items"][0]["node_id"] == 2
    assert (await reader.cutscene_catalog(database, offset=2, limit=1))["items"][0]["node_id"] == 2
    statements = [str(query.compile(dialect=postgresql.dialect())) for query in database.statements]
    assert all(".payload" not in sql for sql in statements)
    assert all(".card" in sql for sql in statements)
    assert any(" < (" in sql and "LIMIT" in sql and "ORDER BY" in sql for sql in statements)
    for cursor, offset, status in (
        (first["next_cursor"], 1, 400),
        (
            encode_cursor(
                [11, 3, 7, 0, 4, "C4", 4], cursor_scope("cutscenes", None, "match", True)
            ),
            0,
            409,
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await reader.cutscene_catalog(database, q="match", cursor=cursor, offset=offset)
        assert error.value.status_code == status


@pytest.mark.asyncio
async def test_notice_listing_filters_and_orders_only_physical_card_not_notice_body(database):
    for identity, category, available in (
        (1, "news", None),
        (2, "news", "2026-10-01"),
        (3, "update", "2026-10-02"),
    ):
        notice = {
            "id": identity,
            "title": f"Notice {identity}",
            "category": category,
            "revision": "r1",
            "available_from": available,
            "related_event_ids": [7],
        }
        database.entry(
            "game_notice",
            identity,
            title=notice["title"],
            card={"notice": notice},
            payload={**notice, "category": "WRONG", "blocks": ["x" * 130_000]},
        )
    page = await reader.notices_page(database, category="news", q="Notice", limit=1)
    assert [item["id"] for item in page["notices"]] == [2]
    assert page["has_more"]
    assert (await reader.notices_page(database, category="news", offset=1))["notices"][0]["id"] == 1
    for query in database.statements:
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert ".payload" not in sql and ".card" in sql
        assert "LIMIT" in sql and "ORDER BY" in sql
    # JSONB containment is PostgreSQL-specific: inspect the actual filtered statement too.
    session = SimpleNamespace(execute=AsyncMock(return_value=result([])))
    assert not (await reader.notices_page(session, event_id=7))["notices"]
    query = session.execute.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "@>" in sql and ".payload" not in sql and ".card" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,call", [("game_notice", reader.notices_page), ("cutscene", reader.cutscene_catalog)]
)
async def test_listing_missing_physical_card_is_explicitly_unready(database, kind, call):
    database.entry(kind, 7, payload={"title": "Legacy payload is not a listing fallback"})
    with pytest.raises(HTTPException) as error:
        await call(database)
    assert error.value.status_code == 503
