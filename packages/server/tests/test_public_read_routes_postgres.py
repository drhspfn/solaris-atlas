"""Public pages work on a database containing projections and no source graph."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.api.routes import search
from wuwa_story.api.routes.story import catalog, character_archive, profiles
from wuwa_story.db.models.browse import (
    CharacterRead,
    CutsceneRead,
    EntityReadRelation,
    GameNoticeRead,
)
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.read_refresh import ReadProjectionScope
from wuwa_story.db.session import get_session
from wuwa_story.read_models import context
from wuwa_story.read_models.publication import publish_entry

URL = os.getenv("WUWA_PUBLIC_READ_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not URL, reason="Disposable local PostgreSQL required"),
]


@pytest_asyncio.fixture
async def read_client(monkeypatch):
    base = create_async_engine(URL, isolation_level="AUTOCOMMIT")
    assert base.url.host in {"localhost", "127.0.0.1"}
    assert base.url.database.endswith("_verification")
    name = "public_reader_" + uuid4().hex
    admin = create_async_engine(base.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    await base.dispose()
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(admin.url.set(database=name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            for schema in ("core", "i18n", "ops"):
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
            for table in (
                Locale.__table__,
                CharacterRead.__table__,
                EntityReadRelation.__table__,
                ReadProjectionScope.__table__,
            ):
                await connection.run_sync(lambda sync, table=table: table.create(sync))
            await connection.execute(
                insert(Locale),
                [
                    {"id": 1, "code": "en", "name": "English"},
                    {"id": 2, "code": "ja", "name": "Japanese"},
                ],
            )
            await connection.execute(
                insert(ReadProjectionScope),
                [
                    {
                        "kind": "character",
                        "snapshot_key": key,
                        "generation": 1,
                        "published_generation": 1,
                        "published_at": datetime.now(UTC),
                        "status": "ready",
                    }
                    for key in ("current", "3.3.0")
                ],
            )
        async with factory() as session, session.begin():
            for locale in (1, 2):
                for snapshot in ("current", "3.3.0"):
                    name = f"Denia {locale} {snapshot}"
                    await publish_entry(
                        session,
                        "character",
                        {
                            "entity_id": 7,
                            "locale_id": locale,
                            "snapshot_key": snapshot,
                            "title": name,
                            "sort_title": name.casefold(),
                            "canonical_key": "character:1211",
                            "slug": "7-denia",
                            "generation": 1,
                            "searchable_text": name,
                            "card": {
                                "aliases": [],
                                "node_type": "character",
                                "href": "/characters/7-denia",
                                "status": "active",
                            },
                            "payload": {
                                "profile": {
                                    "character": {
                                        "canonical_key": "character:1211",
                                        "name": {"content": name},
                                    }
                                },
                                "archive": {"biography": name},
                            },
                        },
                    )
        app = FastAPI()
        for router in (catalog.router, profiles.router, character_archive.router, search.router):
            app.include_router(router)

        async def database():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = database
        monkeypatch.setattr(
            context,
            "get_settings",
            lambda: type("Settings", (), {"read_projections_enabled": True})(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            yield client, factory
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{engine.url.database}"'))
        await admin.dispose()


async def test_catalog_and_both_character_sections_never_require_source_tables(read_client):
    client, _ = read_client
    result = await client.get("/catalog?category=character&locale=en")
    assert result.status_code == 200, result.text
    assert result.json()["results"][0]["title"] == "Denia 1 current"
    for suffix in ("profile", "archive"):
        result = await client.get(f"/characters/7-denia/{suffix}?locale=ja&game_version=3.3.0")
        assert result.status_code == 200, result.text
        assert "Denia 2 3.3.0" in result.text


async def test_previous_publication_remains_available_while_scope_is_pending(read_client):
    client, factory = read_client
    async with factory() as session, session.begin():
        row = await session.get(ReadProjectionScope, ("character", "current"))
        row.generation = 2
        row.status = "pending"
    result = await client.get("/catalog?category=character&locale=en")
    assert result.status_code == 200, result.text
    result = await client.get("/characters/7-denia/profile")
    assert result.status_code == 200, result.text


async def test_search_uses_localized_snapshot_projection_without_source_index(read_client):
    client, _ = read_client
    result = await client.get("/search?q=Denia&category=character&locale=ja&game_version=3.3.0")
    assert result.status_code == 200, result.text
    assert len(result.json()["results"]) == 1
    assert result.json()["results"][0]["title"] == "Denia 2 3.3.0"


async def prepare_listing(factory, model, kind, cards):
    async with factory.kw["bind"].begin() as connection:
        await connection.run_sync(lambda sync: model.__table__.create(sync))
    async with factory() as session, session.begin():
        session.add(
            ReadProjectionScope(
                kind=kind,
                snapshot_key="current",
                generation=1,
                status="ready",
                published_generation=1,
                published_at=datetime.now(UTC),
            )
        )
        for identity, card in cards:
            await publish_entry(
                session,
                kind,
                {
                    "entity_id": identity,
                    "locale_id": 1,
                    "snapshot_key": "current",
                    "generation": 1,
                    "title": f"Scene {identity}",
                    "sort_title": str(identity),
                    "searchable_text": "Scene",
                    "card": card,
                    # List readers must use the compact card, not this page body.
                    "payload": {"unrelated_page_body": "x" * 10000},
                },
            )


async def test_cutscene_keyset_executes_on_postgres_without_playback_bodies(read_client):
    from wuwa_story.read_models.readers.cutscenes import cutscene_catalog

    _, factory = read_client
    await prepare_listing(
        factory,
        CutsceneRead,
        "cutscene",
        [
            (
                identity,
                {
                    "catalog": {
                        "item": {"name": f"C{identity}", "title": f"Scene {identity}"},
                        "cursor_values": [7, 1, 0, 0, identity, f"C{identity}", identity],
                        "playable": True,
                        "release_id": None,
                        "imported_game_versions": ["1.0.0"],
                    }
                },
            )
            for identity in (11, 12, 13)
        ],
    )
    async with factory() as session:
        first = await cutscene_catalog(session, limit=2)
        assert [item["name"] for item in first["items"]] == ["C13", "C12"]
        assert first["total"] == 3 and first["next_cursor"]
        second = await cutscene_catalog(session, limit=2, cursor=first["next_cursor"])
        assert [item["name"] for item in second["items"]] == ["C11"]
        assert second["next_cursor"] is None


async def test_notice_filters_use_compact_cards_without_article_bodies(read_client):
    from wuwa_story.read_models.readers.events import notices_page

    _, factory = read_client
    await prepare_listing(
        factory,
        GameNoticeRead,
        "game_notice",
        [
            (
                identity,
                {
                    "notice": {
                        "id": identity,
                        "title": f"Notice {identity}",
                        "revision": "r1",
                        "category": "news",
                        "related_event_ids": [identity],
                        "available_from": f"2026-10-{identity:02d}T00:00:00Z",
                    }
                },
            )
            for identity in (11, 12)
        ],
    )
    async with factory() as session:
        page = await notices_page(session, category="news", event_id=11)
        assert [item["id"] for item in page["notices"]] == [11]
        assert not page["has_more"]
