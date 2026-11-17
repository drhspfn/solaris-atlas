"""Migration and membership-only backfill against a disposable PostgreSQL database."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
from wuwa_story.ingestion.game_events import CLIENT_SOURCE

from wuwa_story_worker.event_snapshot_backfill import backfill

URL = os.getenv("WUWA_EVENT_SNAPSHOT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="Set WUWA_EVENT_SNAPSHOT_TEST_DATABASE_URL to an empty local verification database",
)
SERVER = Path(__file__).resolve().parents[2] / "server"
BASE = "0035_sequence_video_fallback"
HEAD = "0036_event_snapshots"


async def migrate(revision, *, succeeds=True):
    env = dict(os.environ, DATABASE_URL=URL)
    for key in ("WUWA_DB_TRANSACTION_POOLING", "WUWA_ONLINE_SEARCH_INDEX_ONLY",
                "WUWA_ONLINE_STORAGE_VERIFICATION_ONLY"):
        env.pop(key, None)
    command = "downgrade" if revision == BASE else "upgrade"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", command, revision, cwd=SERVER, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert (process.returncode == 0) == succeeds, (stdout + stderr).decode(
        errors="replace"
    ).replace(URL, "<redacted>")


@pytest_asyncio.fixture
async def database():
    url = make_url(URL)
    assert url.drivername == "postgresql+asyncpg"
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    assert url.database.startswith("event_snapshot_") and url.database.endswith("_verification")
    engine = create_async_engine(URL, poolclass=NullPool)
    created = False
    try:
        async with engine.begin() as conn:
            actual = await conn.scalar(text("SELECT current_database()"))
            assert actual == url.database
            existing = await conn.scalar(text("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_namespace
                    WHERE nspname NOT IN ('public', 'information_schema') AND nspname !~ '^pg_'
                ) OR EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public'
                )
            """))
            assert not existing, "Use a fresh, empty verification database"
            await conn.execute(text("CREATE SCHEMA core"))
            await conn.execute(text("CREATE SCHEMA ops"))
            await conn.execute(text("""
                CREATE TABLE ops.public_cache_revision
                    (table_name text PRIMARY KEY, transaction_id bigint NOT NULL)
            """))
            await conn.execute(text("""
                CREATE FUNCTION ops.bump_public_cache_revision() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    INSERT INTO ops.public_cache_revision AS revision
                        VALUES (TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, txid_current())
                    ON CONFLICT (table_name) DO UPDATE SET transaction_id=EXCLUDED.transaction_id
                        WHERE revision.transaction_id <> EXCLUDED.transaction_id;
                    RETURN NULL;
                END $$
            """))
            await conn.run_sync(GameEvent.__table__.create)
            await conn.run_sync(GameEventOccurrence.__table__.create)
            await conn.execute(text(
                "CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY)"
            ))
            await conn.execute(text("INSERT INTO public.alembic_version VALUES (:version)"), {"version": BASE})
            await conn.execute(text("INSERT INTO ops.public_cache_revision VALUES ('core.game_event', 42)"))
        created = True
        yield engine
    finally:
        if created:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA core CASCADE"))
                await conn.execute(text("DROP SCHEMA ops CASCADE"))
                await conn.execute(text("DROP TABLE public.alembic_version"))
        await engine.dispose()


async def state(session):
    return (
        (await session.execute(text("SELECT to_jsonb(e) FROM core.game_event e ORDER BY id"))).scalars().all(),
        (await session.execute(text("SELECT to_jsonb(o) FROM core.game_event_occurrence o ORDER BY id"))).scalars().all(),
    )


@pytest.mark.asyncio
async def test_0036_upgrade_downgrade_and_membership_backfill(database):
    # A DDL failure after the first CREATE must roll the whole migration back.
    async with database.begin() as conn:
        await conn.execute(text("CREATE TABLE core.game_event_snapshot_member (sentinel integer)"))
    await migrate(HEAD, succeeds=False)
    async with database.begin() as conn:
        assert await conn.scalar(text("SELECT version_num FROM public.alembic_version")) == BASE
        assert await conn.scalar(text("SELECT to_regclass('core.game_event_snapshot')")) is None
        assert await conn.scalar(text("SELECT to_regclass('core.game_event_snapshot_member')")) is not None
        await conn.execute(text("DROP TABLE core.game_event_snapshot_member"))
    await migrate(HEAD)
    async with database.connect() as conn:
        assert await conn.scalar(text("SELECT version_num FROM public.alembic_version")) == HEAD
        assert await conn.scalar(text("""
            SELECT count(*) FROM pg_trigger WHERE tgname='public_cache_changed' AND NOT tgisinternal
            AND tgrelid IN ('core.game_event_snapshot'::regclass, 'core.game_event_snapshot_member'::regclass)
            AND tgtype=60 AND tgfoid='ops.bump_public_cache_revision()'::regprocedure
        """)) == 2
    factory = async_sessionmaker(database, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        rows = [
            GameEvent(source=CLIENT_SOURCE, source_id="activity:100", title="Activity",
                      event_kind="limited", source_url="fixture", source_revision="rev",
                      source_data={"untouched": "activity"}),
            GameEvent(source=CLIENT_SOURCE, source_id="gacha:200", title="Gacha",
                      event_kind="banner", source_url="fixture", source_revision="rev",
                      source_data={"untouched": "gacha"}),
            GameEvent(source="other", source_id="activity:100", title="Other",
                      event_kind="limited", source_url="fixture", source_revision="rev",
                      source_data={"untouched": "other"}),
        ]
        session.add_all(rows)
        await session.flush()
        session.add(GameEventOccurrence(event_id=rows[0].id, source_occurrence_id="schedule",
                                        server="europe", source_data={"untouched": True}))
        await session.commit()
        ids = {row.id for row in rows[:2]}
        original = await state(session)

    plan = {"version": "3.8.0", "id": "download-1"}
    identities = {"activity:100", "gacha:200"}
    async with factory() as session:
        dry = await backfill(session, plan, identities)
        assert dry["matched"] == 2 and dry["missing"] == [] and not dry["applied"]
        missing = await backfill(session, plan, identities | {"activity:missing"}, apply=True)
        assert missing["missing"] == ["activity:missing"] and not missing["applied"]
        await session.commit()
    async with factory() as session:
        assert await session.get(GameEventSnapshot, plan["version"]) is None
        assert await state(session) == original

    # Flushed rows must remain invisible to other transactions and disappear on rollback.
    async with factory() as writer, factory() as observer:
        assert (await backfill(writer, plan, identities, apply=True))["applied"]
        assert await observer.get(GameEventSnapshot, plan["version"]) is None
        await writer.rollback()
    async with factory() as session:
        assert await session.get(GameEventSnapshot, plan["version"]) is None
        assert (await backfill(session, plan, identities, apply=True))["applied"]
        await session.commit()
    async with factory() as session:
        snapshot = await session.get(GameEventSnapshot, plan["version"])
        assert snapshot.complete and snapshot.download_id == plan["id"]
        assert set(await session.scalars(select(GameEventSnapshotMember.event_id))) == ids
        revisions = dict((await session.execute(text("""
            SELECT table_name, transaction_id FROM ops.public_cache_revision
            WHERE table_name LIKE 'core.game_event_snapshot%'
        """))).all())
        assert set(revisions) == {"core.game_event_snapshot", "core.game_event_snapshot_member"}
        assert await state(session) == original

    async with factory() as session:
        assert not (await backfill(session, plan, identities, apply=True))["applied"]
        await session.commit()
        after = dict((await session.execute(text("""
            SELECT table_name, transaction_id FROM ops.public_cache_revision
            WHERE table_name LIKE 'core.game_event_snapshot%'
        """))).all())
        assert after == revisions
        with pytest.raises(ValueError, match="different download"):
            await backfill(session, {**plan, "id": "download-2"}, identities, apply=True)
        await session.rollback()
        with pytest.raises(ValueError, match="differs"):
            await backfill(session, plan, {"activity:100"}, apply=True)
        await session.rollback()
        assert set(await session.scalars(select(GameEventSnapshotMember.event_id))) == ids
        assert await state(session) == original

    # Replacing an incomplete snapshot is atomic; another download cannot
    # replace it, and readers see its old membership until commit.
    partial = {"version": "3.7.9", "id": "download-1"}
    async with factory() as session:
        session.add(GameEventSnapshot(version=partial["version"], download_id=partial["id"], complete=False))
        await session.flush()
        session.add(GameEventSnapshotMember(version=partial["version"], event_id=min(ids)))
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="different download"):
            await backfill(session, {**partial, "id": "download-2"}, identities, apply=True)
        await session.rollback()
        assert not (await backfill(session, partial, identities | {"activity:missing"}, apply=True))["applied"]
        await session.commit()
    async with factory() as writer:
        assert (await backfill(writer, partial, identities, apply=True))["applied"]
        await writer.rollback()
    async with factory() as observer:
        old = await observer.get(GameEventSnapshot, partial["version"])
        assert old and not old.complete
        assert set(await observer.scalars(select(GameEventSnapshotMember.event_id).where(
            GameEventSnapshotMember.version == partial["version"]))) == {min(ids)}
    async with factory() as writer, factory() as observer:
        assert (await backfill(writer, partial, identities, apply=True))["applied"]
        old = await observer.get(GameEventSnapshot, partial["version"])
        assert old and not old.complete
        assert set(await observer.scalars(select(GameEventSnapshotMember.event_id).where(
            GameEventSnapshotMember.version == partial["version"]))) == {min(ids)}
        await writer.commit()
        await observer.rollback()
        new = await observer.get(GameEventSnapshot, partial["version"])
        assert new and new.complete
        assert set(await observer.scalars(select(GameEventSnapshotMember.event_id).where(
            GameEventSnapshotMember.version == partial["version"]))) == ids

    # The importer and backfill share an advisory lock; a competing download
    # must wait for commit and then reject the now-conflicting snapshot.
    other = {"version": "3.8.1", "id": "download-1"}
    async with factory() as first, factory() as second, factory() as observer:
        assert (await backfill(first, other, identities, apply=True))["applied"]
        assert not await observer.scalar(text("SELECT pg_try_advisory_xact_lock(705, 1)"))
        competing = asyncio.create_task(backfill(second, {**other, "id": "download-2"}, identities, apply=True))
        done, _ = await asyncio.wait({competing}, timeout=0.2)
        assert not done
        await first.commit()
        with pytest.raises(ValueError, match="different download"):
            await asyncio.wait_for(competing, timeout=5)
        await second.rollback()
    async with factory() as session:
        assert await state(session) == original

    await migrate(BASE)
    async with database.connect() as conn:
        assert await conn.scalar(text("SELECT version_num FROM public.alembic_version")) == BASE
        assert await conn.scalar(text("SELECT to_regclass('core.game_event_snapshot')")) is None
        assert await conn.scalar(text("SELECT to_regclass('core.game_event_snapshot_member')")) is None
        assert await conn.scalar(text("SELECT count(*) FROM core.game_event")) == 3
        assert await conn.scalar(text("SELECT count(*) FROM core.game_event_occurrence")) == 1
        assert (await conn.execute(text("SELECT table_name, transaction_id FROM ops.public_cache_revision"))).all() == [
            ("core.game_event", 42)
        ]
    await migrate(HEAD)
