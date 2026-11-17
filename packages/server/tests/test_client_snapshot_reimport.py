"""Exercise snapshot preservation with real transactions on an isolated database."""

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from wuwa_story.db.models.game_events import GameEvent, GameEventSnapshot, GameEventSnapshotMember
from wuwa_story.ingestion.client_events import store_client_events


class LocalSession(AsyncSession):
    async def execute(self, statement, *args, **kwargs):
        if str(statement) == "SELECT pg_advisory_xact_lock(705, 1)":
            return None  # SQLite has no advisory locks; production uses PostgreSQL.
        return await super().execute(statement, *args, **kwargs)


@pytest_asyncio.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.execute(text("ATTACH DATABASE ':memory:' AS core"))
        await connection.execute(text("""CREATE TABLE core.game_event (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
            slug TEXT, description TEXT, event_kind TEXT NOT NULL, group_label_key TEXT, game_path TEXT,
            banner_path TEXT, rewards JSON, source_url TEXT NOT NULL, source_revision TEXT NOT NULL,
            source_data JSON NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"""))
        await connection.execute(text("""CREATE TABLE core.game_event_occurrence (
            id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, source_occurrence_id TEXT NOT NULL,
            game_version TEXT, server TEXT NOT NULL, starts_at DATETIME, ends_at DATETIME,
            banner_path TEXT, rewards JSON, season JSON, source_data JSON NOT NULL)"""))
        await connection.execute(text("""CREATE TABLE core.game_event_snapshot (
            version TEXT PRIMARY KEY, download_id TEXT NOT NULL, complete BOOLEAN NOT NULL)"""))
        await connection.execute(text("""CREATE TABLE core.game_event_snapshot_member (
            version TEXT NOT NULL REFERENCES game_event_snapshot(version) ON DELETE CASCADE,
            event_id INTEGER NOT NULL, client_capture JSON, PRIMARY KEY(version,event_id))"""))
        await connection.execute(text("""INSERT INTO core.game_event
            (id, source, source_id, title, event_kind, source_url, source_revision, source_data)
            VALUES (1,'wuwa-game-client','activity:100','First','limited','https://example.test','old','{}'),
                   (2,'wuwa-game-client','activity:101','Second','limited','https://example.test','old','{}')"""))
        await connection.execute(text("""INSERT INTO core.game_event_occurrence
            (event_id,source_occurrence_id,game_version,server,source_data)
            VALUES (1,'existing','3.8','asia','{}'), (1,'existing','3.8','europe','{}'),
                   (1,'existing','3.8','america','{}'), (2,'existing','3.8','asia','{}'),
                   (2,'existing','3.8','europe','{}'), (2,'existing','3.8','america','{}')"""))
        await connection.execute(text("INSERT INTO core.game_event_snapshot VALUES ('3.8.0','pinned',1)"))
        await connection.execute(text("INSERT INTO core.game_event_snapshot_member (version,event_id) VALUES ('3.8.0',1),('3.8.0',2)"))
    yield async_sessionmaker(engine, class_=LocalSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


def record(title):
    return {"event_kind": "limited", "artwork": [], "resources": [],
            "locales": {"en": {"title": title}}}


async def snapshot(session):
    head = await session.get(GameEventSnapshot, "3.8.0")
    members = set(await session.scalars(select(GameEventSnapshotMember.event_id)))
    return head.download_id, head.complete, members


@pytest.mark.asyncio
async def test_partial_reimport_preserves_complete_snapshot_and_members(database):
    async with database() as session:
        result = await store_client_events(session, {"100": record("Updated"), "101": {"locales": {}}},
                                           "3.8.0", "pinned")
        await session.commit()
        assert result["unresolved"]
        assert await snapshot(session) == ("pinned", True, {1, 2})


@pytest.mark.asyncio
async def test_partial_reimport_cannot_add_members_to_complete_snapshot(database):
    async with database() as session:
        with pytest.raises(ValueError, match="differs"):
            await store_client_events(session, {"100": record("First"), "999": record("Unexpected"),
                                                "101": {"locales": {}}}, "3.8.0", "pinned")
        assert await snapshot(session) == ("pinned", True, {1, 2})


@pytest.mark.asyncio
async def test_reimport_rejects_new_download_before_changing_events(database):
    async with database() as session:
        with pytest.raises(ValueError, match="different download"):
            await store_client_events(session, {"100": record("Wrong")}, "3.8.0", "other")
        assert await session.scalar(text("SELECT title FROM core.game_event WHERE id=1")) == "First"
        assert await snapshot(session) == ("pinned", True, {1, 2})


@pytest.mark.asyncio
async def test_complete_reimport_requires_exact_membership(database):
    async with database() as session:
        with pytest.raises(ValueError, match="differs"):
            await store_client_events(session, {"100": record("Wrong")}, "3.8.0", "pinned")
        assert await session.scalar(text("SELECT title FROM core.game_event WHERE id=1")) == "First"
        assert await snapshot(session) == ("pinned", True, {1, 2})
        await store_client_events(session, {"100": record("First updated"), "101": record("Second updated")},
                                  "3.8.0", "pinned")
        await session.commit()
        assert await snapshot(session) == ("pinned", True, {1, 2})
        assert await session.scalar(text("SELECT title FROM core.game_event WHERE id=1")) == "First updated"


@pytest.mark.asyncio
async def test_incomplete_snapshot_can_be_completed_only_by_same_download(database):
    async with database() as session:
        await session.execute(text("UPDATE core.game_event_snapshot SET complete=0 WHERE version='3.8.0'"))
        await session.execute(text("DELETE FROM core.game_event_snapshot_member WHERE event_id=2"))
        first = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        first.client_capture = {**record("First"), "asset_version": "3.8.0", "download_id": "pinned",
                                "schedule_status": "server_controlled"}
        await session.commit()
        with pytest.raises(ValueError, match="different download"):
            await store_client_events(session, {"100": record("Wrong")}, "3.8.0", "other")
        assert await snapshot(session) == ("pinned", False, {1})
        await store_client_events(session, {"100": record("First"), "101": record("Second")},
                                  "3.8.0", "pinned")
        await session.commit()
        # Reload after the bulk delete/insert so ORM identity-map state cannot
        # mask the value persisted by the transaction.
        session.expire_all()
        assert await snapshot(session) == ("pinned", True, {1, 2})
        assert (await session.get(GameEventSnapshotMember, ("3.8.0", 1))).client_capture["locales"]["en"]["title"] == "First"
        assert (await session.get(GameEventSnapshotMember, ("3.8.0", 2))).client_capture["download_id"] == "pinned"


@pytest.mark.asyncio
async def test_versioned_capture_is_idempotent_and_conflicting_payload_fails_closed(database):
    async with database() as session:
        records = {"100": record("First"), "101": record("Second")}
        await store_client_events(session, records, "3.8.0", "pinned")
        await session.commit()
        first = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        assert first.client_capture["asset_version"] == "3.8.0"
        assert first.client_capture["download_id"] == "pinned"
        original = first.client_capture.copy()
        event = await session.get(GameEvent, 1)
        event.source_data = {**event.source_data, "client": {**event.source_data["client"],
                             "prefab_artwork": [{"path": "/Game/Enriched"}]}}
        await session.commit()
        await store_client_events(session, records, "3.8.0", "pinned")
        await session.commit()
        assert first.client_capture == original
        assert event.source_data["client"]["prefab_artwork"] == [{"path": "/Game/Enriched"}]
        with pytest.raises(ValueError, match="capture differs"):
            await store_client_events(session, {"100": record("Different"), "101": record("Second")},
                                      "3.8.0", "pinned")
        assert first.client_capture == original
        assert (await session.get(GameEvent, 1)).title == "First"


@pytest.mark.asyncio
async def test_older_import_retains_newer_current_and_separate_captures(database):
    async with database() as session:
        await store_client_events(session, {"100": record("New"), "101": record("New 2")},
                                  "3.8.0", "pinned")
        await session.commit()
        event = await session.get(GameEvent, 1)
        event.source_data = {**event.source_data, "client": {**event.source_data["client"],
                             "prefab_artwork": [{"path": "/Game/NewOnly"}]}}
        await session.commit()
        await store_client_events(session, {"100": record("Old"), "101": record("Old 2")},
                                  "3.7.0", "older-download")
        await session.commit()
        assert (await session.get(GameEvent, 1)).title == "New"
        old = await session.get(GameEventSnapshotMember, ("3.7.0", 1))
        new = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        assert old.client_capture["locales"]["en"]["title"] == "Old"
        assert old.client_capture["download_id"] == "older-download"
        assert "prefab_artwork" not in old.client_capture
        assert new.client_capture["locales"]["en"]["title"] == "New"
        assert new.client_capture["download_id"] == "pinned"
