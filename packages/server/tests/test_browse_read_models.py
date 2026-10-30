"""Projection schema contracts and exact selection without source-table access."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from wuwa_story.db.models.browse import EntityReadRelation
from wuwa_story.db.repositories import read_models

KINDS = (
    "character",
    "item",
    "location",
    "quest",
    "game_event",
    "game_notice",
    "speaker",
    "dialogue",
    "cutscene",
    "story_map",
)


@pytest.mark.parametrize("kind", KINDS)
def test_entity_projection_schema(kind):
    model = read_models.ENTITY_READ_MODELS[kind]
    table = model.__table__
    assert table.fullname == f"core.{kind}_read"
    assert list(table.primary_key.columns.keys()) == ["entity_id", "locale_id", "snapshot_key"]
    assert set(table.columns.keys()) == {
        "entity_id",
        "locale_id",
        "snapshot_key",
        "title",
        "sort_title",
        "canonical_key",
        "slug",
        "primary_image_file_id",
        "primary_image_url",
        "artwork",
        "payload",
        "generation",
        "updated_at",
        "card",
        "role_type",
        "quest_type",
        "searchable_text",
        "search_vector",
    }
    assert not table.foreign_keys
    for name in ("title", "sort_title", "payload", "generation", "updated_at", "artwork", "card"):
        assert not table.c[name].nullable
    assert isinstance(table.c.generation.type, BigInteger)
    assert table.c.generation.default is table.c.generation.server_default is None
    assert isinstance(table.c.payload.type, JSONB)
    assert table.c.payload.type.none_as_null
    assert table.c.payload.default is table.c.payload.server_default is None
    assert isinstance(table.c.artwork.type, JSONB)
    assert table.c.updated_at.type.timezone
    assert table.c.snapshot_key.default.arg == "current"
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "payload JSONB NOT NULL" in ddl
    assert "generation BIGINT NOT NULL" in ddl


def test_relation_identity_includes_kinds_direction_type_and_snapshot():
    table = EntityReadRelation.__table__
    assert table.fullname == "core.entity_read_relation"
    assert list(table.primary_key.columns.keys()) == [
        "owner_kind",
        "owner_id",
        "related_kind",
        "related_id",
        "relation_type",
        "snapshot_key",
    ]
    assert not table.foreign_keys
    assert isinstance(table.c.evidence.type, JSONB)
    assert not table.c.evidence.nullable
    assert set(read_models.ENTITY_READ_MODELS) == set(KINDS)


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS core")
        # Only projection tables exist: any accidental source join fails execution.
        for kind in KINDS:
            connection.exec_driver_sql(f"""
                CREATE TABLE core.{kind}_read (
                    entity_id INTEGER NOT NULL, locale_id INTEGER NOT NULL,
                    snapshot_key TEXT NOT NULL DEFAULT 'current', title TEXT NOT NULL,
                    sort_title TEXT NOT NULL, canonical_key TEXT, slug TEXT, card JSON NOT NULL, role_type INTEGER, quest_type TEXT, searchable_text TEXT NOT NULL DEFAULT '', search_vector TEXT,
                    primary_image_file_id INTEGER, primary_image_url TEXT,
                    artwork JSON NOT NULL DEFAULT '[]', payload JSON NOT NULL,
                    generation BIGINT NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (entity_id, locale_id, snapshot_key)
                )
            """)
        connection.exec_driver_sql("""
            CREATE TABLE core.entity_read_relation (
                owner_kind TEXT NOT NULL, owner_id INTEGER NOT NULL, related_kind TEXT NOT NULL,
                related_id INTEGER NOT NULL, relation_type TEXT NOT NULL,
                snapshot_key TEXT NOT NULL DEFAULT 'current', evidence JSON NOT NULL DEFAULT '{}',
                PRIMARY KEY (owner_kind, owner_id, related_kind, related_id, relation_type, snapshot_key)
            )
        """)
        with Session(bind=connection) as sync:

            class LocalSession:
                statements = []

                async def scalar(self, statement):
                    self.statements.append(statement)
                    return sync.scalar(statement)

                async def scalars(self, statement):
                    self.statements.append(statement)
                    return sync.scalars(statement)

                async def execute(self, statement):
                    self.statements.append(statement)
                    return sync.execute(statement)

                def entry(
                    self,
                    kind,
                    entity_id,
                    locale_id=1,
                    snapshot_key="current",
                    title="Entry",
                    payload=None,
                    canonical_key=None,
                    slug=None,
                    card=None,
                    role_type=None,
                    quest_type=None,
                ):
                    stored_card = {
                        "status": "active",
                        **(
                            card
                            if card is not None
                            else (payload or {}).get(
                                "card",
                                {"node_type": kind, "aliases": [], "href": "/nodes/example"},
                            )
                        ),
                    }
                    if (
                        kind == "game_event"
                        and payload is not None
                        and "calendar" not in stored_card
                    ):
                        stored_card["calendar"] = payload
                    connection.exec_driver_sql(
                        f"INSERT INTO core.{kind}_read (entity_id, locale_id, snapshot_key, title, sort_title, payload, generation, canonical_key, slug, card, role_type, quest_type) VALUES (?, ?, ?, ?, ?, ?, 7, ?, ?, ?, ?, ?)",
                        (
                            entity_id,
                            locale_id,
                            snapshot_key,
                            title,
                            title.casefold(),
                            json.dumps(
                                {"kind": kind, "title": title} if payload is None else payload
                            ),
                            canonical_key,
                            slug,
                            json.dumps(stored_card),
                            role_type
                            if role_type is not None
                            else (payload or {}).get("card", {}).get("role_type"),
                            quest_type
                            if quest_type is not None
                            else (payload or {})
                            .get("card", {})
                            .get(
                                "quest_type",
                                (payload or {})
                                .get("profile", {})
                                .get("quest", {})
                                .get("quest_type"),
                            ),
                        ),
                    )

                def relation(
                    self,
                    owner_kind,
                    owner_id,
                    related_kind,
                    related_id,
                    relation_type="appears_in",
                    snapshot_key="current",
                ):
                    connection.exec_driver_sql(
                        "INSERT INTO core.entity_read_relation VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            owner_kind,
                            owner_id,
                            related_kind,
                            related_id,
                            relation_type,
                            snapshot_key,
                            json.dumps({"source": "projected"}),
                        ),
                    )

            yield LocalSession()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_entry_selects_exact_kind_identity_locale_snapshot_without_fallback(database, kind):
    database.entry(kind, 1)
    database.entry(kind, 1, locale_id=2, title="Other locale")
    database.entry(kind, 1, snapshot_key="3.10", title="Historical")
    current = await read_models.read_entry(database, kind, 1, 1)
    assert current.payload == {"kind": kind, "title": "Entry"}
    assert current.generation == 7
    assert (await read_models.read_entry(database, kind, 1, 2)).title == "Other locale"
    assert (await read_models.read_entry(database, kind, 1, 1, "3.10")).title == "Historical"
    assert await read_models.read_entry(database, kind, 1, 3) is None
    assert await read_models.read_entry(database, kind, 1, 1, "3.9") is None
    assert await read_models.read_entry(database, kind, 2, 1) is None
    for statement in database.statements:
        assert [table.fullname for table in statement.get_final_froms()] == [f"core.{kind}_read"]


@pytest.mark.asyncio
async def test_list_count_and_stable_page_order_share_locale_snapshot_filters(database):
    for identity, title in [(1, "Zulu"), (3, "alpha"), (2, "Alpha")]:
        database.entry("location", identity, title=title)
    database.entry("location", 4, locale_id=2, title="Excluded locale")
    database.entry("location", 5, snapshot_key="3.10", title="Historical")
    total, first = await read_models.browse_entries(database, "location", 1, limit=2)
    assert total == 3
    assert [row.entity_id for row in first] == [2, 3]
    total, second = await read_models.browse_entries(database, "location", 1, offset=2)
    assert total == 3 and [row.entity_id for row in second] == [1]
    assert await read_models.browse_entries(database, "location", 1, "missing") == (0, [])
    assert await read_models.browse_entries(database, "location", 1, offset=100) == (3, [])
    total, history = await read_models.browse_entries(database, "location", 1, "3.10")
    assert total == 1 and history[0].entity_id == 5
    count, page = database.statements[:2]
    assert str(count.whereclause) == str(page.whereclause)
    sql = str(page.compile(dialect=postgresql.dialect()))
    assert "ORDER BY core.location_read.sort_title, core.location_read.entity_id" in sql
    assert "LIMIT" in sql and "OFFSET" in sql and "JOIN" not in sql


@pytest.mark.asyncio
async def test_relations_select_exact_owner_kind_direction_and_snapshot(database):
    database.relation("character", 1, "quest", 5)
    database.relation("character", 1, "quest", 6)
    database.relation("game_event", 1, "quest", 5)
    database.relation("character", 2, "quest", 5)
    database.relation("quest", 5, "character", 1)
    database.relation("character", 1, "quest", 7, snapshot_key="3.10")
    database.relation("character", 1, "game_notice", 5, relation_type="mentioned_by")
    rows = await read_models.read_relations(database, "character", 1, related_kind="quest")
    assert [row.related_id for row in rows] == [5, 6]
    assert rows[0].evidence == {"source": "projected"}
    rows = await read_models.read_relations(database, "character", 1, "3.10")
    assert [row.related_id for row in rows] == [7]
    assert await read_models.read_relations(database, "character", 1, "missing") == []
    rows = await read_models.read_relations(database, "character", 1, relation_type="mentioned_by")
    assert len(rows) == 1 and rows[0].related_kind == "game_notice"
    rows = await read_models.read_relations(
        database, "character", 1, related_kind="quest", limit=1, offset=1
    )
    assert [row.related_id for row in rows] == [6]
    for statement in database.statements:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "JOIN" not in sql and "core.entity_read_relation" in sql
        assert "snapshot_key =" in sql


@pytest.mark.asyncio
async def test_invalid_kind_and_pagination_do_not_query_sources():
    session = SimpleNamespace(scalar=AsyncMock(), scalars=AsyncMock())
    for operation in (
        read_models.read_entry(session, "unknown", 1, 1),
        read_models.browse_entries(session, "unknown", 1),
        read_models.browse_entries(session, "item", 1, limit=0),
        read_models.browse_entries(session, "item", 1, offset=-1),
        read_models.read_relations(session, "unknown", 1),
        read_models.read_relations(session, "item", 1, related_kind="unknown"),
        read_models.read_relations(session, "item", 1, limit=101),
    ):
        with pytest.raises(ValueError):
            await operation
    session.scalar.assert_not_awaited()
    session.scalars.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
def test_search_access_paths_are_on_new_read_tables_only(kind):
    table = read_models.ENTITY_READ_MODELS[kind].__table__
    assert table.c.search_vector.computed.persisted
    assert not table.c.searchable_text.nullable
    assert "to_tsvector" in str(table.c.search_vector.computed.sqltext)
    expected = {
        f"ix_{kind}_read_search_vector",
        f"ix_{kind}_read_title_trgm",
        f"ix_{kind}_read_searchable_text_trgm",
    }
    indexes = {index.name for index in table.indexes}
    assert indexes.isdisjoint(expected) if kind == "story_map" else expected <= indexes
    assert {f"ix_{kind}_read_{suffix}" for suffix in ("catalog", "canonical", "slug")} <= indexes
    if kind == "character":
        assert "ix_character_read_role_catalog" in indexes
    if kind == "quest":
        assert "ix_quest_read_type_catalog" in indexes
