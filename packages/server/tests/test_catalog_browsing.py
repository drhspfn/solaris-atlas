"""Execute catalog pagination and fallback reads against an isolated in-memory database."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.selectable import Subquery

from wuwa_story.api.routes.story import catalog
from wuwa_story.story import service


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        for schema in ("graph", "core", "search", "i18n"):
            connection.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {schema}")
        for ddl in (
            "graph.node_type (id INTEGER PRIMARY KEY, key TEXT)",
            "graph.node (id INTEGER PRIMARY KEY, type_id INTEGER, canonical_key TEXT, slug TEXT, status TEXT)",
            "search.document (id INTEGER PRIMARY KEY, target_node_id INTEGER, locale_id INTEGER, category TEXT, title TEXT, aliases JSON)",
            "i18n.locale (id INTEGER PRIMARY KEY, code TEXT, name TEXT)",
            "i18n.localization_key (id INTEGER PRIMARY KEY, key TEXT)",
            "i18n.localization_content (id INTEGER PRIMARY KEY, content TEXT)",
            "i18n.localization_value (key_id INTEGER, locale_id INTEGER, release_id INTEGER, content_id INTEGER, status TEXT, source_record_id INTEGER)",
        ):
            connection.exec_driver_sql(f"CREATE TABLE {ddl}")
        for entity in ("character", "item", "location", "quest", "speaker"):
            connection.exec_driver_sql(
                f"CREATE TABLE core.{entity} (node_id INTEGER PRIMARY KEY, name_key_id INTEGER, canonical_name TEXT, quest_type TEXT, metadata JSON)"
            )
        connection.exec_driver_sql("INSERT INTO i18n.locale VALUES (1, 'en', 'English')")
        connection.exec_driver_sql(
            "INSERT INTO graph.node_type VALUES (1, 'character'), (2, 'item'), (3, 'area'), (4, 'location'), (5, 'quest'), (6, 'speaker')"
        )
        with Session(bind=connection) as sync:

            class LocalSession:
                info = sync.info
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

                def node(
                    self,
                    identity,
                    kind,
                    *,
                    name=None,
                    role=None,
                    quest_type=None,
                    status="active",
                    key_id=None,
                    search=None,
                ):
                    type_id = {
                        "character": 1,
                        "item": 2,
                        "area": 3,
                        "location": 4,
                        "quest": 5,
                        "speaker": 6,
                    }[kind]
                    connection.exec_driver_sql(
                        "INSERT INTO graph.node VALUES (?, ?, ?, ?, ?)",
                        (identity, type_id, f"{kind}:{identity}", f"{identity}-entry", status),
                    )
                    entity = "location" if kind == "area" else kind
                    connection.exec_driver_sql(
                        f"INSERT INTO core.{entity} VALUES (?, ?, ?, ?, ?)",
                        (
                            identity,
                            key_id,
                            name,
                            quest_type,
                            json.dumps({"role_type": role} if role is not None else {}),
                        ),
                    )
                    if search is not None:
                        self.title(identity, kind, search)

                def title(self, identity, category, title):
                    connection.exec_driver_sql(
                        "INSERT INTO search.document (target_node_id, locale_id, category, title, aliases) VALUES (?, 1, ?, ?, ?)",
                        (identity, category, title, json.dumps(["alias"])),
                    )

                def localized(self, key_id, text, release_id=3, status="resolved_nonempty"):
                    connection.exec_driver_sql(
                        "INSERT OR IGNORE INTO i18n.localization_key VALUES (?, ?)",
                        (key_id, f"name:{key_id}"),
                    )
                    result = connection.exec_driver_sql(
                        "INSERT INTO i18n.localization_content (content) VALUES (?)",
                        (text,),
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO i18n.localization_value VALUES (?, 1, ?, ?, ?, 99)",
                        (key_id, release_id, result.lastrowid, status),
                    )

            monkeypatch.setattr(catalog, "_release_id", AsyncMock(return_value=3))
            monkeypatch.setattr(catalog, "entity_image_urls", AsyncMock(return_value={}))
            monkeypatch.setattr(
                service,
                "releases",
                AsyncMock(
                    return_value={
                        1: SimpleNamespace(id=1, game_version="3.9", sequence=10),
                        3: SimpleNamespace(id=3, game_version="3.10", sequence=2),
                        4: SimpleNamespace(id=4, game_version="3.11", sequence=3),
                    }
                ),
            )
            yield LocalSession()
    engine.dispose()


async def browse(
    database, category="location", *, limit=24, offset=0, quest_type_id=None, locale="en"
):
    return await catalog.browse_catalog(
        category=category,
        quest_type_id=quest_type_id,
        locale=locale,
        limit=limit,
        offset=offset,
        session=database,
    )


@pytest.mark.asyncio
async def test_location_pagination_retains_search_order_area_types_and_response_shape(database):
    database.node(10, "area", name="Canonical Z", search="beta")
    database.node(11, "location", name="Canonical A", search="Alpha")
    database.node(12, "area", search="alpha")
    database.node(13, "location", status="inactive", search="aardvark")
    first = await browse(database, limit=2)
    second = await browse(database, limit=2, offset=2)
    assert [item["id"] for item in first["results"]] == [11, 12]
    assert [item["id"] for item in second["results"]] == [10]
    assert first["total"] == second["total"] == 3
    assert first == {
        "category": "location",
        "quest_type_id": None,
        "locale": "en",
        "total": 3,
        "limit": 2,
        "offset": 0,
        "results": [
            {
                "id": identity,
                "canonical_key": f"{kind}:{identity}",
                "node_type": kind,
                "category": "location",
                "title": title,
                "aliases": ["alias"],
                "slug": f"{identity}-entry",
                "href": f"/locations/{identity}-entry",
                "image_url": None,
            }
            for identity, kind, title in [(11, "location", "Alpha"), (12, "area", "alpha")]
        ],
    }
    assert (await browse(database, offset=100))["results"] == []
    catalog.entity_image_urls.assert_awaited_with(database, [])


@pytest.mark.asyncio
async def test_primary_roles_and_quest_filter_apply_before_pagination_and_to_single_count(database):
    for identity, role in [(1, 2), (2, 1), (3, None), (4, 9)]:
        database.node(identity, "character", role=role, search=str(identity))
    result = await browse(database, "character", limit=1)
    assert result["total"] == 2
    assert [row["id"] for row in result["results"]] == [2]
    assert sum("count(" in str(statement) for statement in database.statements) == 1
    assert [row["id"] for row in (await browse(database, "character", offset=1))["results"]] == [3]
    database.node(20, "quest", quest_type="1", search="Alpha")
    database.node(21, "quest", quest_type="3", search="Beta")
    database.node(22, "quest", quest_type="3", status="inactive", search="Gamma")
    database.statements.clear()
    result = await browse(database, "quest", quest_type_id=3)
    assert result["total"] == 1
    assert [row["id"] for row in result["results"]] == [21]
    assert sum("count(" in str(statement) for statement in database.statements) == 1


@pytest.mark.asyncio
async def test_title_precedence_placeholder_rejection_and_category_fallbacks(database):
    placeholder = "Please contact our customer service"
    for identity in range(1, 7):
        database.node(
            identity,
            "location",
            key_id=identity,
            name="Canonical" if identity <= 3 else None,
            search="Search" if identity == 1 else placeholder,
        )
    database.localized(1, "Localized")
    database.localized(2, "Localized")
    database.localized(3, placeholder)
    database.localized(4, placeholder)
    database.localized(5, "Historical", release_id=1)
    database.localized(5, "Future", release_id=4)
    database.title(4, "location_nickname", "Nickname")
    database.node(7, "area")
    result = await browse(database)
    assert {row["id"]: row["title"] for row in result["results"]} == {
        1: "Search",
        2: "Localized",
        3: "Canonical",
        4: "Nickname",
        5: "Historical",
        6: "Unnamed location · 6",
        7: "Area · 7",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,label",
    [
        ("character", "Unnamed character"),
        ("item", "Unnamed item"),
        ("quest", "Quest"),
        ("speaker", "Unnamed speaker"),
    ],
)
async def test_category_specific_hydration_and_fallback_labels(database, category, label):
    database.node(1, category)
    assert (await browse(database, category))["results"][0]["title"] == f"{label} · 1"
    page = database.statements[2]
    sql = str(page.compile(dialect=postgresql.dialect()))
    for other in {"character", "item", "location", "quest", "speaker"} - {category}:
        assert f"JOIN core.{other} " not in sql
    assert "LIMIT" in sql and "catalog_page" in sql
    assert "graph.node.metadata" not in sql
    selection = next(
        clause.element
        for clause in visitors.iterate(page)
        if isinstance(clause, Subquery) and clause.name == "catalog_page"
    )
    selection_sql = str(selection.compile(dialect=postgresql.dialect()))
    assert "LIMIT" in selection_sql and "ORDER BY lower(coalesce(" in selection_sql
    assert "i18n.localization" not in selection_sql
    assert "_nickname" not in str(selection.compile().params)
    if category != "character":
        assert "JOIN core." not in selection_sql


@pytest.mark.asyncio
async def test_fallback_localization_queries_are_batched_for_whole_page(database):
    for identity in range(1, 25):
        database.node(identity, "location", key_id=identity)
        database.localized(identity, f"Historical {identity}", release_id=1)
    result = await browse(database)
    assert len(result["results"]) == 24
    assert {row["title"] for row in result["results"]} == {f"Historical {i}" for i in range(1, 25)}
    sql = [str(statement) for statement in database.statements]
    assert sum("FROM i18n.localization_key" in statement for statement in sql) == 1
    assert sum("FROM i18n.localization_value" in statement for statement in sql) == 1
    assert len(sql) == 6  # locale, count, page, fallback locale, keys, values


@pytest.mark.asyncio
async def test_invalid_locale_and_quest_filter_rejected_before_catalog_reads(database):
    for options in ({"locale": "xx"}, {"quest_type_id": 3}):
        database.statements.clear()
        with pytest.raises(HTTPException) as error:
            await browse(database, **options)
        assert error.value.status_code == 400
        assert len(database.statements) == 1
