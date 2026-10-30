"""Lease races and trigger behaviour on a disposable local PostgreSQL database."""

import asyncio
import json
import os
from time import perf_counter
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from test_read_refresh import migration
from wuwa_story.db.models.read_refresh import ReadProjectionDirty, ReadProjectionScope
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.read_models import refresh
from wuwa_story.read_models import worker as refresh_worker

URL = os.getenv("WUWA_READ_REFRESH_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="Disposable local PostgreSQL URL required")]


@pytest_asyncio.fixture
async def database(request):
    engine = create_async_engine(URL)
    assert engine.url.host in {"localhost", "127.0.0.1"}
    assert engine.url.database.endswith("_read_refresh_verification")
    # Each test owns a new database; no existing source schemas are touched.
    name = "read_refresh_" + uuid4().hex
    admin = create_async_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    await engine.dispose()
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(engine.url.set(database=name))
    module = migration()
    try:
        async with engine.begin() as connection:
            # The real 0001 foundation installs this before creating ORM tables.
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            for schema in ("core", "ops", "graph", "storage", "i18n", "search", "raw", "content", "story"):
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
            sources = {**module.DIRECT_TABLES, **module.NODE_TABLES, **module.STORAGE_TABLES}
            sources["raw.source_record"] = ()
            sources.update({table: () for table in module.ANALYSIS_ONLY_TABLES})
            for table in sources:
                await connection.execute(text(f"""CREATE TABLE {table} (
                    id bigint PRIMARY KEY, node_id bigint, type_id smallint,
                    owner_node_id bigint, file_id bigint, release_id bigint,
                    from_node_id bigint, to_node_id bigint, target_node_id bigint,
                    speaker_node_id bigint, entity_node_id bigint,
                    document_id bigint, edge_id bigint, quest_node_id bigint,
                    event_id bigint, notice_id bigint, media_asset_node_id bigint,
                    game_version text, locale text, reference_type text,
                    quest_type text,
                    status text NOT NULL DEFAULT 'active',
                    source text NOT NULL DEFAULT 'wuwa-game-client',
                    available boolean DEFAULT true, is_primary boolean DEFAULT true,
                    backend text DEFAULT 's3', bucket text DEFAULT 'test', object_key text,
                    metadata jsonb DEFAULT '{{}}', source_data jsonb DEFAULT '{{}}',
                    related_event_ids jsonb DEFAULT '[]'
                )"""))
            # This table was added after 0008 and deliberately has no revision
            # trigger: 0024 must register it without touching existing triggers.
            await connection.execute(text("CREATE TABLE i18n.localization_value (id bigint PRIMARY KEY, content text)"))
            for statement in (
                "CREATE TABLE graph.node_type (id smallint PRIMARY KEY, key text)",
                "CREATE TABLE ops.game_release (id bigint PRIMARY KEY, game_version text)",
                "CREATE TABLE ops.import_run (id bigint PRIMARY KEY, release_id bigint, importer text, status text)",
                "CREATE TABLE i18n.locale (id smallint PRIMARY KEY, code text, name text)",
                "CREATE TABLE raw_stub (id bigint)",
                "CREATE TABLE ops.public_cache_revision (table_name text PRIMARY KEY, transaction_id bigint NOT NULL)",
                """CREATE FUNCTION ops.bump_public_cache_revision() RETURNS trigger LANGUAGE plpgsql AS $$
                   BEGIN INSERT INTO ops.public_cache_revision VALUES (TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,txid_current())
                   ON CONFLICT(table_name) DO UPDATE SET transaction_id=EXCLUDED.transaction_id;
                   RETURN NULL; END; $$""",
                "INSERT INTO graph.node_type VALUES (1,'character'),(2,'item'),(3,'dialogue_line'),(4,'quest')",
                "INSERT INTO graph.node(id,type_id) VALUES(7,1),(8,1),(9,2),(10,4)",
                "INSERT INTO i18n.locale VALUES (1,'en','English'),(2,'ja','Japanese')",
            ):
                await connection.execute(text(statement))
            for table in (*sources, "ops.import_run", "ops.game_release"):
                await connection.execute(text(f"CREATE TRIGGER public_cache_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION ops.bump_public_cache_revision()"))
            if getattr(request, "param", 0):
                count = int(request.param)
                await connection.execute(text(f"INSERT INTO ops.game_release(id,game_version) SELECT n,'historical-'||n::text FROM generate_series(1,{count}) n"))
                await connection.execute(text(f"INSERT INTO ops.import_run(id,release_id,importer,status) SELECT n,n,'wuwa-deterministic-compiler','succeeded' FROM generate_series(1,{count}) n"))
            def apply(sync):
                module.op = Operations(MigrationContext.configure(sync))
                module.upgrade()
            await connection.run_sync(apply)
            module.seeded_scopes = set((await connection.execute(text(
                "SELECT kind,snapshot_key FROM ops.read_projection_dirty"
            ))).all())
            await connection.execute(text("DELETE FROM ops.read_projection_dirty"))
        yield async_sessionmaker(engine, expire_on_commit=False), engine, module
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


async def dirty(factory, ids=(7,), kind="character", snapshot="current"):
    async with factory() as session, session.begin():
        await refresh.mark_dirty(session, kind, ids, snapshot, debounce_seconds=0)


async def due(factory):
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE ops.read_projection_dirty SET available_at=clock_timestamp()"))


def entry(identity=7, locale=1, title="fresh"):
    return {"entity_id": identity, "locale_id": locale, "snapshot_key": "current",
            "title": title, "sort_title": title, "card": {"title": title},
            "searchable_text": title, "payload": {"title": title}}


async def test_concurrent_claims_skip_locked_and_commit_before_build(database):
    factory, _, _ = database
    await dirty(factory, (7, 8))
    async with factory() as blocker, blocker.begin():
        await blocker.scalar(select(ReadProjectionDirty).where(
            ReadProjectionDirty.entity_id == 7,
        ).with_for_update())
        claims = await refresh.claim_refreshes(factory)
        assert [item.entity_id for item in claims] == [8]
    claims += await refresh.claim_refreshes(factory)
    assert {item.entity_id for item in claims} == {7, 8}
    assert await refresh.claim_refreshes(factory) == []
    async with factory() as session:
        assert (await session.get(ReadProjectionDirty, ("character", 8, "current"))).lease_token == claims[0].lease_token


async def test_new_write_fences_publish_and_expired_worker_cannot_steal_lease(database):
    factory, _, _ = database
    await dirty(factory)
    old = (await refresh.claim_refreshes(factory))[0]
    await dirty(factory)
    assert not await refresh.publish_refresh(factory, old, [entry()])
    await refresh.release_claim(factory, old)
    fresh = (await refresh.claim_refreshes(factory))[0]
    assert fresh.generation == 2
    assert await refresh.publish_refresh(factory, fresh, [entry()])
    await dirty(factory)
    expired = (await refresh.claim_refreshes(factory))[0]
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE ops.read_projection_dirty SET lease_until=clock_timestamp()-interval '1 second'"))
    successor = (await refresh.claim_refreshes(factory))[0]
    assert expired.lease_token != successor.lease_token
    assert not await refresh.renew_lease(factory, expired)
    await refresh.release_claim(factory, expired)
    assert not await refresh.publish_refresh(factory, expired, [entry(title="stale")])
    assert await refresh.publish_refresh(factory, successor, [entry(title="newest")])
    async with factory() as session:
        assert (await session.get(ENTITY_READ_MODELS["character"], (7, 1, "current"))).title == "newest"
        row = await session.get(ReadProjectionDirty, ("character", 7, "current"))
        assert row.requested_generation == row.processed_generation == 3


async def test_bulk_expands_individual_generations_and_deleted_published_ids(database):
    factory, _, _ = database
    await dirty(factory, (7,))
    direct = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, direct, [entry()])
    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM graph.node WHERE id=7"))
    await dirty(factory, (0,))
    bulk = next(item for item in await refresh.claim_refreshes(factory) if item.entity_id == 0)
    assert await refresh.expand_bulk(factory, bulk, batch_size=1)
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty).where(ReadProjectionDirty.kind == "character")))
        assert {row.entity_id for row in rows} == {0, 7, 8}
        assert next(row for row in rows if row.entity_id == 7).requested_generation > direct.generation
    # Bulk never writes a read table or copies its generation into entity jobs.
    assert await refresh.publish_refresh(factory, (await refresh.claim_refreshes(factory, limit=1))[0], [])


async def test_source_and_dirty_rollback_together_and_storage_never_dirties_all_characters(database):
    factory, _, _ = database
    async with factory() as session:
        await session.execute(text("INSERT INTO core.character(id,node_id) VALUES(7,7)"))
        assert await session.get(ReadProjectionDirty, ("character", 7, "current")) is not None
        await session.rollback()
    async with factory() as session:
        assert await session.get(ReadProjectionDirty, ("character", 7, "current")) is None
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO storage.file_reference(id,owner_node_id,file_id,reference_type) VALUES(1,7,11,'voice')"))
        await session.execute(text("INSERT INTO storage.file_location(id,file_id) VALUES(1,11)"))
        await session.execute(text("UPDATE storage.file_location SET available=false WHERE id=1"))
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty)))
        assert {(row.kind, row.entity_id) for row in rows} == {("character", 7)}
    await due(factory)
    first = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, first, [entry()])
    async with factory() as session:
        row = await session.get(ReadProjectionDirty, ("character", 7, "current"))
        assert row.processed_generation == row.requested_generation
        assert len(list(await session.scalars(select(ReadProjectionDirty)))) == 1


async def test_refresh_build_sees_committed_claim_and_publishes_locales_together(database):
    factory, _, _ = database
    await dirty(factory)
    claimed = (await refresh.claim_refreshes(factory))[0]
    async def builder(_session, kind, identity, locale, snapshot):
        async with factory() as observer:
            row = await observer.get(ReadProjectionDirty, (kind, identity, snapshot))
            assert row.lease_token == claimed.lease_token
        return entry(identity, locale)
    assert await refresh.refresh_claim(factory, claimed, builder=builder, relation_builder=AsyncMock(return_value=[]), locale_batch_size=1)
    async with factory() as session:
        assert len(list(await session.scalars(select(ENTITY_READ_MODELS["character"])))) == 2


async def test_shared_changes_mark_bulk_scopes_but_media_bookkeeping_is_targeted(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO raw.source_record(id) VALUES(1)"))
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty)))
        assert {(row.kind, row.entity_id) for row in rows} == {(kind, 0) for kind in refresh.READ_PROJECTION_KINDS}
        generations = {row.kind: row.requested_generation for row in rows}
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.voice_reference(id,node_id) VALUES(7,7)"))
        await session.execute(text("INSERT INTO storage.file_reference(id,owner_node_id,file_id) VALUES(1,7,11)"))
    async with factory() as session:
        for kind, generation in generations.items():
            assert (await session.get(ReadProjectionDirty, (kind, 0, "current"))).requested_generation == generation
        assert await session.get(ReadProjectionDirty, ("character", 7, "current")) is not None


async def test_retry_preserves_published_data_and_new_write_wakes_backoff(database):
    factory, _, _ = database
    await dirty(factory)
    first = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, first, [entry(title="previous")])
    await dirty(factory)
    failed = (await refresh.claim_refreshes(factory))[0]
    await refresh.fail_claim(factory, failed, RuntimeError("private payload"))
    assert await refresh.claim_refreshes(factory) == []
    async with factory() as session:
        row = await session.get(ReadProjectionDirty, ("character", 7, "current"))
        assert row.requested_generation == 2 and row.processed_generation == 1
        assert row.last_error == "RuntimeError" and row.lease_token is None
        assert (await session.get(ENTITY_READ_MODELS["character"], (7, 1, "current"))).title == "previous"
    await dirty(factory)
    assert (await refresh.claim_refreshes(factory))[0].generation == 3


@pytest.mark.parametrize("selected_snapshots,occurrence_version", [
    (("3.6",), "3.6"),
    (("3.6.0",), "3.6"),
    (("3.6",), "3.6.1"),
    (("03.06.0",), "3.6"),
    (("3.6", "3.6.0"), "3.6"),
])
async def test_download_bulk_normalizes_into_current_and_dated_entity_scopes(
    database, selected_snapshots, occurrence_version,
):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.game_event(id,source_data) VALUES (50,'{\"client\":{\"download_id\":\"new\"}}'),(51,'{}')"))
        await session.execute(text("INSERT INTO core.game_event_occurrence(id,event_id,game_version,source_data) VALUES (1,50,:version,'{\"client\":{\"download_id\":\"old\"}}')"),
                              {"version": occurrence_version})
        for snapshot in (*selected_snapshots, "3.60.0", "3.7.0"):
            await refresh.mark_dirty(session, "game_event", [0], snapshot, debounce_seconds=0)
        await session.execute(text("DELETE FROM ops.read_projection_dirty"))
    await dirty(factory, (0,), "game_event", "download:old")
    bulk = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.expand_bulk(factory, bulk, batch_size=1)
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty).where(ReadProjectionDirty.entity_id > 0)))
        assert {(row.entity_id, row.snapshot_key) for row in rows} == {
            (50, "current"), *((50, snapshot) for snapshot in selected_snapshots),
        }


async def test_partial_bulk_restart_keeps_entity_generations_independent(database, monkeypatch):
    factory, _, _ = database
    await dirty(factory, (0,))
    bulk = (await refresh.claim_refreshes(factory))[0]
    original = refresh.mark_dirty
    calls = 0
    async def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted expansion")
        await original(*args, **kwargs)
    monkeypatch.setattr(refresh, "mark_dirty", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await refresh.expand_bulk(factory, bulk, batch_size=1)
    monkeypatch.setattr(refresh, "mark_dirty", original)
    async with factory() as session:
        assert await session.get(ReadProjectionDirty, ("character", 7, "current")) is not None
        assert await session.get(ReadProjectionDirty, ("character", 8, "current")) is None
    await refresh.release_claim(factory, bulk)
    restarted = next(row for row in await refresh.claim_refreshes(factory) if row.entity_id == 0)
    assert await refresh.expand_bulk(factory, restarted, batch_size=1)
    async with factory() as session:
        assert (await session.get(ReadProjectionDirty, ("character", 7, "current"))).requested_generation == 2
        assert (await session.get(ReadProjectionDirty, ("character", 8, "current"))).requested_generation == 1


async def test_only_matching_snapshot_with_live_import_lease_is_deferred(database):
    factory, engine, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO ops.game_release(id,game_version) VALUES(1,'3.6')"))
        await session.execute(text("INSERT INTO ops.import_run(id,release_id,importer,status) VALUES(1,1,'wuwa-deterministic-compiler','running')"))
        await session.execute(text("DELETE FROM ops.read_projection_dirty"))
    await dirty(factory)
    await dirty(factory, snapshot="3.6")
    await dirty(factory, snapshot="3.5")
    await dirty(factory, (0,), "game_notice")
    async with engine.connect() as lease:
        await lease.execute(text("SELECT pg_advisory_lock(527631,1)"))
        try:
            claims = await refresh.claim_refreshes(factory)
            assert {(claim.kind, claim.snapshot_key) for claim in claims} == {
                ("character", "3.5"), ("game_notice", "current"),
            }
        finally:
            await lease.execute(text("SELECT pg_advisory_unlock(527631,1)"))
    # A stale running status, without its live worker lease, blocks no snapshot.
    resumed = await refresh.claim_refreshes(factory)
    assert resumed and {claim.snapshot_key for claim in resumed} == {"3.6", "current"}


async def test_heartbeat_renews_lease_while_builder_runs_without_publication_lock(database):
    factory, _, _ = database
    await dirty(factory)
    claimed = (await refresh.claim_refreshes(factory, lease_seconds=3))[0]
    async with factory() as session:
        initial = (await session.get(ReadProjectionDirty, ("character", 7, "current"))).lease_until
    async def slow_builder(_session, _kind, identity, locale, _snapshot):
        await asyncio.sleep(1.2)
        async with factory() as session:
            row = await session.get(ReadProjectionDirty, ("character", 7, "current"))
            assert row.lease_until > initial
        return entry(identity, locale)
    assert await refresh.refresh_claim(factory, claimed, builder=slow_builder, relation_builder=AsyncMock(return_value=[]), lease_seconds=3)


async def test_publication_failure_rolls_back_every_locale_and_acknowledgement(database):
    factory, _, _ = database
    await dirty(factory)
    claimed = (await refresh.claim_refreshes(factory))[0]
    with pytest.raises(ValueError, match="Incomplete"):
        await refresh.publish_refresh(factory, claimed, [entry(), {
            "entity_id": 7, "locale_id": 2, "snapshot_key": "current",
        }])
    async with factory() as session:
        assert list(await session.scalars(select(ENTITY_READ_MODELS["character"]))) == []
        assert (await session.get(ReadProjectionDirty, ("character", 7, "current"))).processed_generation == 0


async def test_write_waiting_during_publication_remains_pending_after_ack(database, monkeypatch):
    factory, _, _ = database
    await dirty(factory)
    claimed = (await refresh.claim_refreshes(factory))[0]
    original = refresh.publish_entry
    started = asyncio.Event()
    writer = None
    async def concurrent_write():
        started.set()
        await dirty(factory)
    async def publish(*args):
        nonlocal writer
        writer = asyncio.create_task(concurrent_write())
        await started.wait()
        return await original(*args)
    monkeypatch.setattr(refresh, "publish_entry", publish)
    assert await refresh.publish_refresh(factory, claimed, [entry()])
    await writer
    async with factory() as session:
        row = await session.get(ReadProjectionDirty, ("character", 7, "current"))
        assert row.processed_generation == 1 and row.requested_generation == 2


async def test_migration_downgrade_removes_own_tables_and_triggers(database):
    _, engine, module = database
    async with engine.begin() as connection:
        def revert(sync):
            module.op = Operations(MigrationContext.configure(sync))
            module.downgrade()
        await connection.run_sync(revert)
        assert await connection.scalar(text("SELECT to_regclass('ops.read_projection_dirty')")) is None
        assert await connection.scalar(text("SELECT to_regclass('core.character_read')")) is None
        assert await connection.scalar(text("SELECT to_regclass('core.character')")) is not None


async def test_debounce_coalesces_entity_writes_and_keeps_active_lease(database):
    factory, _, _ = database
    await dirty(factory)
    claimed = (await refresh.claim_refreshes(factory))[0]
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.character(id,node_id) VALUES(7,7)"))
    async with factory() as session:
        first = await session.get(ReadProjectionDirty, ("character", 7, "current"))
        generation, deadline = first.requested_generation, first.available_at
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE core.character SET metadata=jsonb_build_object('changed',true) WHERE id=7"))
    async with factory() as session:
        latest = await session.get(ReadProjectionDirty, ("character", 7, "current"))
        assert latest.requested_generation > generation
        assert latest.available_at >= deadline
        assert latest.lease_token == claimed.lease_token
        assert len(list(await session.scalars(select(ReadProjectionDirty)))) == 1
    assert not await refresh.publish_refresh(factory, claimed, [entry()])
    await refresh.release_claim(factory, claimed)
    assert await refresh.claim_refreshes(factory) == []
    await due(factory)
    assert len(await refresh.claim_refreshes(factory)) == 1


async def test_scope_requires_complete_bulk_coverage_and_records_valid_empty_catalog(database):
    factory, _, _ = database
    await dirty(factory, (0,))
    bulk = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.expand_bulk(factory, bulk, batch_size=1)
    claims = await refresh.claim_refreshes(factory)
    assert len(claims) == 2
    assert await refresh.publish_refresh(factory, claims[0], [entry(claims[0].entity_id)])
    async with factory() as session:
        partial = await session.get(ReadProjectionScope, ("character", "current"))
        assert partial.status != "ready" and partial.published_generation == 0
    assert await refresh.publish_refresh(factory, claims[1], [entry(claims[1].entity_id)])
    async with factory() as session:
        complete = await session.get(ReadProjectionScope, ("character", "current"))
        assert complete.status == "ready" and complete.published_generation > 0
        published = complete.published_generation
    await dirty(factory, (7,))
    async with factory() as session:
        updating = await session.get(ReadProjectionScope, ("character", "current"))
        assert updating.status == "pending" and updating.published_generation == published
        assert updating.published_at is not None
        assert await session.get(ENTITY_READ_MODELS["character"], (7, 1, "current")) is not None
    await dirty(factory, (0,), "game_notice")
    empty = next(claim for claim in await refresh.claim_refreshes(factory) if claim.kind == "game_notice")
    assert await refresh.expand_bulk(factory, empty)
    async with factory() as session:
        empty_scope = await session.get(ReadProjectionScope, ("game_notice", "current"))
        assert empty_scope.status == "ready" and empty_scope.published_generation > 0
        assert list(await session.scalars(select(ENTITY_READ_MODELS["game_notice"]))) == []


async def test_uninitialized_scope_does_not_become_ready_from_one_entry(database):
    factory, _, _ = database
    await dirty(factory)
    one = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, one, [entry()])
    async with factory() as session:
        assert (await session.get(ReadProjectionScope, ("character", "current"))).status == "pending"


async def test_old_published_snapshot_is_dirtied_on_delete_and_empty_backfill_becomes_ready(database):
    factory, _, _ = database
    await dirty(factory, (0,), snapshot="3.6")
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.character_read(entity_id,locale_id,snapshot_key,title,sort_title,card,payload,generation) VALUES(7,1,'3.6','Historical','historical','{}','{}',1)"))
        await session.execute(text("DELETE FROM graph.node WHERE id=7"))
    async with factory() as session:
        assert await session.get(ReadProjectionDirty, ("character", 7, "3.6")) is not None
    await dirty(factory, (0,), snapshot="3.6")
    await due(factory)
    bulk = next(row for row in await refresh.claim_refreshes(factory) if row.entity_id == 0)
    assert await refresh.expand_bulk(factory, bulk)
    # Earlier claims were deliberately abandoned; reclaim them as a real crash.
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE ops.read_projection_dirty SET lease_until=clock_timestamp()-interval '1 second' WHERE lease_token IS NOT NULL"))
    claims = await refresh.claim_refreshes(factory)
    historical = next(row for row in claims if row.snapshot_key == "3.6" and row.entity_id == 7)
    assert await refresh.publish_refresh(factory, historical, [])
    async with factory() as session:
        assert await session.get(ENTITY_READ_MODELS["character"], (7, 1, "3.6")) is None
        assert (await session.get(ReadProjectionScope, ("character", "3.6"))).status == "ready"


async def test_association_updates_dirty_old_and_new_endpoints_without_bulk_character_work(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO graph.edge(id,from_node_id,to_node_id) VALUES(1,7,9)"))
        await session.execute(text("UPDATE graph.edge SET from_node_id=8 WHERE id=1"))
        await session.execute(text("DELETE FROM graph.edge WHERE id=1"))
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty)))
        assert {(row.kind, row.entity_id) for row in rows} == {("character", 7), ("character", 8), ("item", 9)}


async def test_later_localization_table_gets_revision_trigger_and_dirties_snapshots(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO ops.game_release(id,game_version) VALUES(1,'3.6')"))
        await session.execute(text("INSERT INTO ops.import_run(id,release_id,importer,status) VALUES(1,1,'wuwa-deterministic-compiler','succeeded')"))
        for kind in ("character", "dialogue", "story_map"):
            await refresh.mark_dirty(session, kind, [0], "3.6", debounce_seconds=0)
        await session.execute(text("DELETE FROM ops.read_projection_dirty"))
        await session.execute(text("INSERT INTO i18n.localization_value VALUES(1,'updated localized text')"))
    async with factory() as session:
        identities = {(row.kind, row.entity_id, row.snapshot_key)
                      for row in await session.scalars(select(ReadProjectionDirty))}
        assert ("character", 0, "current") in identities
        assert ("character", 0, "3.6") in identities
        assert ("dialogue", 0, "3.6") in identities
        assert ("story_map", 0, "current") in identities
        assert ("story_map", 0, "3.6") in identities
        for kind in ("location", "speaker", "game_notice"):
            assert (kind, 0, "3.6") not in identities
        for kind in ("item", "quest", "cutscene", "game_event"):
            assert (kind, 0, "3.6") not in identities


async def test_story_map_bulk_uses_quest_types_and_does_not_depend_on_storage(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.quest(id,node_id,quest_type) VALUES(7,7,'3'),(8,8,'other'),(9,9,'7')"))
        assert await session.get(ReadProjectionDirty, ("story_map", 0, "current")) is not None
        await session.execute(text("DELETE FROM ops.read_projection_dirty"))
    await dirty(factory, (0,), "story_map")
    bulk = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.expand_bulk(factory, bulk, batch_size=1)
    async with factory() as session:
        ids = list(await session.scalars(select(ReadProjectionDirty.entity_id).where(
            ReadProjectionDirty.kind == "story_map", ReadProjectionDirty.entity_id > 0,
        ).order_by(ReadProjectionDirty.entity_id)))
        assert ids == [1, 4, 8]
    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM ops.read_projection_dirty"))
        await session.execute(text("INSERT INTO storage.file_reference(id,owner_node_id,file_id) VALUES(1,7,11)"))
        await session.execute(text("INSERT INTO storage.file_location(id,file_id) VALUES(1,11)"))
        assert await session.get(ReadProjectionDirty, ("story_map", 0, "current")) is None


async def test_relations_replace_once_for_all_locales_and_rollback_as_one_unit(database, monkeypatch):
    factory, engine, _ = database
    await dirty(factory)
    claimed = (await refresh.claim_refreshes(factory))[0]
    statements = []
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if "core.entity_read_relation" in statement:
            statements.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", observe)
    relations = [{"related_kind": "item", "related_id": 9, "relation_type": "uses", "evidence": {}}]
    try:
        assert await refresh.publish_refresh(factory, claimed, [entry(locale=1), entry(locale=2)], relations)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observe)
    assert len([sql for sql in statements if sql.startswith("DELETE")]) == 1
    assert len([sql for sql in statements if sql.startswith("INSERT")]) == 1
    await dirty(factory)
    retry = (await refresh.claim_refreshes(factory))[0]
    original = refresh.publish_entry
    async def fail_second(session, kind, value, links):
        if value["locale_id"] == 2:
            raise RuntimeError("publication failed")
        return await original(session, kind, value, links)
    monkeypatch.setattr(refresh, "publish_entry", fail_second)
    with pytest.raises(RuntimeError, match="publication failed"):
        await refresh.publish_refresh(factory, retry, [entry(title="new", locale=1), entry(title="new", locale=2)], relations)
    async with factory() as session:
        assert (await session.get(ENTITY_READ_MODELS["character"], (7, 1, "current"))).title == "fresh"
        assert (await session.get(ReadProjectionDirty, ("character", 7, "current"))).processed_generation == claimed.generation


async def test_migration_matches_model_columns_and_normal_projection_access_keys(database):
    _, engine, _ = database
    async with engine.connect() as connection:
        def verify(sync):
            inspector = inspect(sync)
            for kind, model in ENTITY_READ_MODELS.items():
                columns = inspector.get_columns(model.__tablename__, schema="core")
                assert {column["name"] for column in columns} == set(model.__table__.columns.keys())
                assert next(column for column in columns if column["name"] == "card")["nullable"] is False
                indexes = {index["name"]: index for index in inspector.get_indexes(model.__tablename__, schema="core")}
                assert indexes[f"ix_{kind}_read_catalog"]["column_names"] == ["locale_id", "snapshot_key", "sort_title", "entity_id"]
                assert indexes[f"ix_{kind}_read_canonical"]["column_names"] == ["locale_id", "snapshot_key", "canonical_key"]
                assert indexes[f"ix_{kind}_read_slug"]["column_names"] == ["locale_id", "snapshot_key", "slug"]
            for model in (ReadProjectionDirty, ReadProjectionScope):
                assert {column["name"] for column in inspector.get_columns(model.__tablename__, schema="ops")} == set(model.__table__.columns.keys())
        await connection.run_sync(verify)


async def test_generated_search_vector_refreshes_on_localized_text_update(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.item_read(entity_id,locale_id,snapshot_key,title,sort_title,card,payload,generation,searchable_text) VALUES(9,1,'current','Old title','old','{}','{}',1,'old description')"))
        assert await session.scalar(text("SELECT entity_id FROM core.item_read WHERE search_vector @@ plainto_tsquery('simple','old description')")) == 9
        await session.execute(text("UPDATE core.item_read SET title='New title', searchable_text='Localized snow lotus' WHERE entity_id=9"))
        assert await session.scalar(text("SELECT entity_id FROM core.item_read WHERE search_vector @@ plainto_tsquery('simple','snow lotus')")) == 9
        assert await session.scalar(text("SELECT entity_id FROM core.item_read WHERE search_vector @@ plainto_tsquery('simple','old description')")) is None
        generated = await session.scalar(text("SELECT attgenerated FROM pg_attribute WHERE attrelid='core.item_read'::regclass AND attname='search_vector'"))
        assert generated == b"s" or generated == "s"


async def test_unknown_snapshot_never_publishes_false_empty_coverage(database):
    factory, _, _ = database
    await dirty(factory, (0,), snapshot="not-imported")
    bulk = (await refresh.claim_refreshes(factory))[0]
    with pytest.raises(ValueError, match="Snapshot not imported"):
        await refresh.expand_bulk(factory, bulk)
    async with factory() as session:
        assert (await session.get(ReadProjectionScope, ("character", "not-imported"))).published_generation == 0


@pytest.mark.parametrize("kind,identity,detail", [
    ("character", 7, "character source record not found"),
    ("quest", 10, "quest source not found"),
])
async def test_nested_builder_404_preserves_existing_entity_publication(database, kind, identity, detail):
    factory, _, _ = database
    await dirty(factory, (identity,), kind)
    first = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, first, [entry(identity, title="previous good")])
    await dirty(factory, (identity,), kind)
    retry = (await refresh.claim_refreshes(factory))[0]
    builder = AsyncMock(side_effect=HTTPException(404, detail))
    with pytest.raises(HTTPException) as failed:
        await refresh.refresh_claim(factory, retry, builder=builder, relation_builder=AsyncMock(return_value=[]))
    assert failed.value.detail == detail
    async with factory() as session:
        assert (await session.get(ENTITY_READ_MODELS[kind], (identity, 1, "current"))).title == "previous good"
        row = await session.get(ReadProjectionDirty, (kind, identity, "current"))
        assert row.processed_generation == first.generation
        assert row.requested_generation == retry.generation
        assert row.last_error == "HTTPException"
        assert row.lease_token is row.lease_until is None


@pytest.mark.parametrize("kind,identity,delete_sql", [
    ("character", 7, "DELETE FROM graph.node WHERE id=7"),
    ("character", 7, "UPDATE graph.node SET status='inactive' WHERE id=7"),
    ("game_event", 50, "DELETE FROM core.game_event WHERE id=50"),
])
async def test_proven_source_deletion_or_inactivity_allows_tombstone(database, kind, identity, delete_sql):
    factory, _, _ = database
    if kind == "game_event":
        async with factory() as session, session.begin():
            await session.execute(text("INSERT INTO core.game_event(id) VALUES(50)"))
    await dirty(factory, (identity,), kind)
    await due(factory)
    first = next(row for row in await refresh.claim_refreshes(factory) if row.kind == kind and row.entity_id == identity)
    assert await refresh.publish_refresh(factory, first, [entry(identity, title="previous good")])
    async with factory() as session, session.begin():
        await session.execute(text(delete_sql))
    await due(factory)
    deleted = next(row for row in await refresh.claim_refreshes(factory) if row.kind == kind and row.entity_id == identity)
    assert await refresh.refresh_claim(
        factory, deleted, builder=AsyncMock(side_effect=HTTPException(404, "entity missing")),
        relation_builder=AsyncMock(return_value=[]),
    )
    async with factory() as session:
        assert await session.get(ENTITY_READ_MODELS[kind], (identity, 1, "current")) is None
        row = await session.get(ReadProjectionDirty, (kind, identity, "current"))
        assert row.processed_generation == row.requested_generation


async def test_missing_snapshot_metadata_404_is_not_deletion_proof(database):
    factory, _, _ = database
    await dirty(factory, (7,), snapshot="missing-version")
    await due(factory)
    claimed = next(row for row in await refresh.claim_refreshes(factory) if row.entity_id == 7)
    historical = {**entry(), "snapshot_key": "missing-version"}
    assert await refresh.publish_refresh(factory, claimed, [historical])
    await dirty(factory, (7,), snapshot="missing-version")
    second = next(row for row in await refresh.claim_refreshes(factory) if row.entity_id == 7)
    with pytest.raises(HTTPException):
        await refresh.refresh_claim(
            factory, second, builder=AsyncMock(side_effect=HTTPException(404, "snapshot not imported")),
            relation_builder=AsyncMock(return_value=[]),
        )
    async with factory() as session:
        assert await session.get(ENTITY_READ_MODELS["character"], (7, 1, "missing-version")) is not None


async def test_deleted_event_tombstones_history_after_capture_metadata_is_gone(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.game_event(id) VALUES(50)"))
        await session.execute(text("INSERT INTO core.game_event_occurrence(id,event_id,game_version) VALUES(1,50,'3.6')"))
    await dirty(factory, (50,), "game_event", "3.6")
    await due(factory)
    first = next(row for row in await refresh.claim_refreshes(factory)
                 if row.kind == "game_event" and row.entity_id == 50 and row.snapshot_key == "3.6")
    historical = {**entry(50), "snapshot_key": "3.6"}
    assert await refresh.publish_refresh(factory, first, [historical])
    async with factory() as session, session.begin():
        # The minimal source fixture has no FK cascade; mirror the real cascade.
        await session.execute(text("DELETE FROM core.game_event_occurrence WHERE event_id=50"))
        await session.execute(text("DELETE FROM core.game_event WHERE id=50"))
    await due(factory)
    deleted = next(row for row in await refresh.claim_refreshes(factory)
                   if row.kind == "game_event" and row.entity_id == 50 and row.snapshot_key == "3.6")
    assert await refresh.refresh_claim(
        factory, deleted, builder=AsyncMock(side_effect=HTTPException(404, "event missing")),
        relation_builder=AsyncMock(return_value=[]),
    )
    async with factory() as session:
        assert await session.get(ENTITY_READ_MODELS["game_event"], (50, 1, "3.6")) is None


async def test_thousand_owner_canonical_and_reference_batches_coalesce_with_measured_cost(database):
    factory, _, _ = database
    started = perf_counter()
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO graph.node(id,type_id) SELECT n,1 FROM generate_series(1001,2000) n"))
        await session.execute(text("INSERT INTO core.character(id,node_id) SELECT n,n FROM generate_series(1001,2000) n"))
    canonical_ms = (perf_counter() - started) * 1000
    async with factory() as session:
        before = {row.entity_id: row.requested_generation
                  for row in await session.scalars(select(ReadProjectionDirty))}
        assert len(before) == 1000 and set(before.values()) == {2}
    started = perf_counter()
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO storage.file_reference(id,owner_node_id,file_id,reference_type) SELECT n,n,n,'entity_image' FROM generate_series(1001,2000) n"))
    reference_ms = (perf_counter() - started) * 1000
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty)))
        assert len(rows) == 1000
        assert all(row.kind == "character" and row.entity_id in before for row in rows)
        assert all(row.requested_generation == before[row.entity_id] + 1 for row in rows)
        assert all(row.processed_generation == 0 and row.lease_token is None for row in rows)
    print(json.dumps({"owners": 1000, "canonical_transaction_ms": round(canonical_ms, 2),
                      "reference_transaction_ms": round(reference_ms, 2), "dirty_rows": 1000}))


@pytest.mark.parametrize("database", [21], indirect=True)
async def test_rollout_and_source_changes_never_activate_unselected_history(database):
    factory, _, module = database
    assert module.seeded_scopes == {(kind, "current") for kind in refresh.READ_PROJECTION_KINDS}
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO raw.source_record(id) VALUES(1)"))
        await session.execute(text("INSERT INTO graph.node_revision(id,node_id,release_id) VALUES(1,7,1)"))
        await session.execute(text("INSERT INTO storage.file_reference(id,owner_node_id,release_id,file_id) VALUES(1,7,1,11)"))
    async with factory() as session:
        assert set(await session.scalars(select(ReadProjectionDirty.snapshot_key))) == {"current"}
        assert set(await session.scalars(select(ReadProjectionScope.snapshot_key))) == {"current"}
    # Explicitly selecting one bounded snapshot enables its later invalidation,
    # without materializing any of the other 20 imported versions.
    await dirty(factory, (0,), "character", "historical-1")
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO raw.source_record(id) VALUES(2)"))
    async with factory() as session:
        assert set(await session.scalars(select(ReadProjectionDirty.snapshot_key))) == {"current", "historical-1"}
        selected = await session.get(ReadProjectionDirty, ("character", 0, "historical-1"))
        assert selected.requested_generation > 1


async def test_bulk_budget_rejects_before_fanout_and_estimate_is_read_only(database):
    factory, _, _ = database
    estimate = await refresh.estimate_backfill(factory, "character")
    assert (estimate.entities, estimate.locales, estimate.rows) == (2, 2, 4)
    assert estimate.sampled_rows == 0 and estimate.estimated_bytes == 4 * 65536 * 2
    async with factory() as session:
        assert list(await session.scalars(select(ReadProjectionDirty))) == []
    await dirty(factory, (0,))
    bulk = (await refresh.claim_refreshes(factory))[0]
    with pytest.raises(refresh.BackfillBudgetExceeded):
        await refresh.refresh_claim(factory, bulk, max_rows=1)
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty)))
        assert len(rows) == 1 and rows[0].entity_id == 0
        assert rows[0].processed_generation == 0
        assert "exceed backfill budget" in rows[0].last_error
        assert rows[0].lease_token is None


async def test_event_source_membership_matches_client_and_numeric_patch_selection(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.game_event(id,source) VALUES(50,'wuwa-game-client'),(51,'legacy'),(52,'wuwa-game-client'),(53,'wuwa-game-client')"))
        await session.execute(text("INSERT INTO core.game_event_occurrence(id,event_id,game_version) VALUES(1,50,'3.6'),(2,51,'3.6'),(3,52,'3.7')"))
        assert set(await session.scalars(refresh._source_ids("game_event"))) == {50, 52, 53}
        assert set(await session.scalars(refresh._source_ids("game_event", "3.6.0"))) == {50}
        assert set(await session.scalars(refresh._source_ids("game_event", "03.06"))) == {50}
        assert set(await session.scalars(refresh._source_ids("game_event", "3.7"))) == {52}
        assert list(await session.scalars(refresh._source_ids("game_event", "3.60"))) == []


async def test_analysis_document_and_head_only_dirty_their_owner(database):
    factory, _, module = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO content.document(id,node_id) VALUES(1,10)"))
        await session.execute(text("INSERT INTO content.document_head(id,node_id,document_id) VALUES(1,10,1)"))
        await session.execute(text("INSERT INTO content.document_reference(id,document_id) VALUES(1,1)"))
        await session.execute(text("UPDATE content.document SET metadata=jsonb_build_object('analysis','updated') WHERE id=1"))
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty)))
        assert {(row.kind, row.entity_id) for row in rows} == {("quest", 10)}
        generation = rows[0].requested_generation
    async with factory() as session, session.begin():
        for table in module.ANALYSIS_ONLY_TABLES:
            await session.execute(text(f"INSERT INTO {table}(id) VALUES(1)"))
    async with factory() as session:
        rows = list(await session.scalars(select(ReadProjectionDirty)))
        assert len(rows) == 1 and rows[0].requested_generation == generation


async def test_edge_evidence_refreshes_exact_endpoints_without_global_fanout(database):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO graph.edge(id,from_node_id,to_node_id) VALUES(1,10,9)"))
        await session.execute(text("DELETE FROM ops.read_projection_dirty"))
        await session.execute(text("INSERT INTO graph.edge_evidence(id,edge_id) VALUES(1,1)"))
    async with factory() as session:
        assert {(row.kind, row.entity_id) for row in await session.scalars(select(ReadProjectionDirty))} == {
            ("quest", 10), ("item", 9),
        }


async def test_import_started_after_claim_fences_current_publication(database):
    factory, engine, _ = database
    await dirty(factory)
    first = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, first, [entry(title="previous good")])
    await dirty(factory)
    building = (await refresh.claim_refreshes(factory))[0]
    async with engine.connect() as lease:
        await lease.execute(text("SELECT pg_advisory_lock(527631,1)"))
        try:
            async with factory() as session, session.begin():
                await session.execute(text("INSERT INTO ops.game_release(id,game_version) VALUES(1,'3.6')"))
                await session.execute(text("INSERT INTO ops.import_run(id,release_id,importer,status) VALUES(1,1,'wuwa-deterministic-compiler','running')"))
            assert not await refresh.publish_refresh(factory, building, [entry(title="half imported")])
            async with factory() as session:
                assert (await session.get(ENTITY_READ_MODELS["character"], (7, 1, "current"))).title == "previous good"
                row = await session.get(ReadProjectionDirty, ("character", 7, "current"))
                assert row.processed_generation == first.generation
        finally:
            await lease.execute(text("SELECT pg_advisory_unlock(527631,1)"))
    await refresh.release_claim(factory, building)


async def test_approved_budgets_bootstrap_bounded_sample_before_full_fanout(database, monkeypatch):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO graph.node(id,type_id) SELECT n,1 FROM generate_series(1001,2000) n"))
        await session.execute(text("DELETE FROM ops.read_projection_dirty"))
    settings = refresh.get_settings().model_copy(update={
        "read_projection_max_rows": 2004,
        "read_projection_max_bytes": 32 * 1024**2,
    })
    monkeypatch.setattr(refresh, "get_settings", lambda: settings)
    await dirty(factory, (0,))
    bulk = (await refresh.claim_refreshes(factory))[0]
    # No worker arguments are required: approved operational budgets are read
    # from settings, but the unsampled estimate cannot fan out everything.
    assert not await refresh.refresh_claim(factory, bulk)
    async with factory() as session:
        jobs = list(await session.scalars(select(ReadProjectionDirty)))
        assert len(jobs) == 4
        assert sum(row.entity_id > 0 for row in jobs) == 3
        assert (await session.get(ReadProjectionScope, ("character", "current"))).published_generation == 0
    samples = await refresh.claim_refreshes(factory)
    assert len(samples) == 3
    for sample in samples:
        assert await refresh.publish_refresh(factory, sample, [entry(sample.entity_id)])
    estimate = await refresh.estimate_backfill(factory, "character")
    assert estimate.sampled_rows == 3
    assert estimate.estimated_bytes < 32 * 1024**2
    await due(factory)
    resumed = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.refresh_claim(factory, resumed)
    async with factory() as session:
        jobs = list(await session.scalars(select(ReadProjectionDirty)))
        assert len(jobs) == 1003
        assert (await session.get(ReadProjectionScope, ("character", "current"))).published_generation == 0


async def test_exact_pilot_claim_skips_older_bulk_and_other_snapshots(database):
    factory, _, _ = database
    await dirty(factory, (0,), "item")
    await dirty(factory, (9,), "item", "3.6.0")
    await dirty(factory, (9,), "item")
    claimed = await refresh.claim_refreshes(
        factory, limit=10, kinds=["item"], entity_ids=[9], snapshot_key="current",
    )
    assert [(row.entity_id, row.snapshot_key) for row in claimed] == [(9, "current")]
    async with factory() as session:
        assert (await session.get(ReadProjectionDirty, ("item", 0, "current"))).lease_token is None
        assert (await session.get(ReadProjectionDirty, ("item", 9, "3.6.0"))).lease_token is None


async def test_worker_database_limits_are_local_and_cancel_long_queries_and_locks(database):
    factory, engine, _ = database
    limited_engine, limited = refresh_worker.create_worker_database(
        engine.url, statement_timeout_ms=500, lock_timeout_ms=20,
    )
    default_engine, defaults = refresh_worker.create_worker_database(engine.url)
    try:
        async with factory() as session:
            before = await session.scalar(text("SHOW statement_timeout"))
        async with defaults() as session:
            assert await session.scalar(text("SELECT setting::int FROM pg_settings WHERE name='statement_timeout'")) == 60000
            assert await session.scalar(text("SELECT setting::int FROM pg_settings WHERE name='lock_timeout'")) == 1000
        async with limited() as session:
            with pytest.raises(DBAPIError) as cancelled:
                await session.execute(text("SELECT pg_sleep(5)"))
            assert cancelled.value.orig.sqlstate == "57014"
        await dirty(factory)
        async with factory() as blocker, blocker.begin():
            await blocker.execute(text("SELECT * FROM ops.read_projection_dirty FOR UPDATE"))
            async with limited() as session:
                with pytest.raises(DBAPIError) as locked:
                    await session.execute(text("SELECT * FROM ops.read_projection_dirty FOR UPDATE"))
                assert locked.value.orig.sqlstate == "55P03"
        async with factory() as session:
            assert await session.scalar(text("SHOW statement_timeout")) == before
    finally:
        await limited_engine.dispose()
        await default_engine.dispose()


async def test_worker_runtime_timeout_persists_retry_and_stops_heartbeat(database, monkeypatch):
    factory, _, _ = database
    await dirty(factory)
    first = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, first, [entry(title="previous good")])
    await dirty(factory)
    unwound = asyncio.Event()

    async def blocked(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            unwound.set()

    original = refresh.refresh_claim

    async def build(create, claim):
        return await original(create, claim, builder=blocked, relation_builder=AsyncMock(return_value=[]), lease_seconds=3)

    monkeypatch.setattr(refresh_worker, "refresh_claim", build)
    assert await refresh_worker.refresh_batch(factory, kinds=["character"], claim_timeout_seconds=0.05) == 1
    assert unwound.is_set()
    assert not any("refresh_claim.<locals>.heartbeat" in task.get_coro().__qualname__
                   for task in asyncio.all_tasks() if not task.done())
    async with factory() as session:
        row = await session.get(ReadProjectionDirty, ("character", 7, "current"))
        assert row.last_error == "TimeoutError"
        assert row.lease_token is row.lease_until is None
        assert row.processed_generation == first.generation < row.requested_generation
        assert (await session.get(ENTITY_READ_MODELS["character"], (7, 1, "current"))).title == "previous good"


async def test_selected_locale_refresh_preserves_other_locales_and_estimates_selected_rows(database, monkeypatch):
    factory, _, _ = database
    await dirty(factory)
    first = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, first, [entry(title="old EN"), entry(locale=2, title="old JA")])
    settings = refresh.get_settings().model_copy(update={"read_projection_locales": ("en",)})
    monkeypatch.setattr(refresh, "get_settings", lambda: settings)
    estimate = await refresh.estimate_backfill(factory, "character")
    assert estimate.locales == 1
    assert estimate.rows == estimate.entities
    await dirty(factory)
    claimed = (await refresh.claim_refreshes(factory))[0]
    builder = AsyncMock(side_effect=lambda _session, _kind, identity, locale, _snapshot: entry(identity, locale, "new EN"))
    assert await refresh.refresh_claim(factory, claimed, builder=builder, relation_builder=AsyncMock(return_value=[]))
    assert [call.args[3] for call in builder.call_args_list] == [1]
    async with factory() as session:
        model = ENTITY_READ_MODELS["character"]
        en = await session.get(model, (7, 1, "current"))
        ja = await session.get(model, (7, 2, "current"))
        assert (en.title, en.generation) == ("new EN", claimed.generation)
        assert (ja.title, ja.generation) == ("old JA", first.generation)
    # The default complete publication retains its pruning contract.
    await dirty(factory)
    full = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, full, [entry(title="full EN")])
    async with factory() as session:
        assert await session.get(model, (7, 2, "current")) is None


async def test_selected_locale_refresh_proven_deletion_removes_all_locales(database, monkeypatch):
    factory, _, _ = database
    await dirty(factory)
    first = (await refresh.claim_refreshes(factory))[0]
    assert await refresh.publish_refresh(factory, first, [entry(), entry(locale=2)])
    settings = refresh.get_settings().model_copy(update={"read_projection_locales": ("en",)})
    monkeypatch.setattr(refresh, "get_settings", lambda: settings)
    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM graph.node WHERE id=7"))
    await due(factory)
    claimed = (await refresh.claim_refreshes(factory, entity_ids=[7]))[0]
    assert await refresh.refresh_claim(
        factory, claimed, builder=AsyncMock(side_effect=HTTPException(404, "deleted")),
        relation_builder=AsyncMock(return_value=[]),
    )
    async with factory() as session:
        model = ENTITY_READ_MODELS["character"]
        assert list(await session.scalars(select(model).where(model.entity_id == 7))) == []


async def test_selected_locale_refresh_acknowledges_unselected_notice_without_deletion(database, monkeypatch):
    factory, _, _ = database
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO core.game_notice(id,locale) VALUES(71,'ja')"))
    await dirty(factory, (71,), "game_notice")
    first = (await refresh.claim_refreshes(factory, entity_ids=[71]))[0]
    assert await refresh.publish_refresh(factory, first, [entry(71, 2, "JA notice")])
    await dirty(factory, (71,), "game_notice")
    settings = refresh.get_settings().model_copy(update={"read_projection_locales": ("en",)})
    monkeypatch.setattr(refresh, "get_settings", lambda: settings)
    claimed = (await refresh.claim_refreshes(factory, entity_ids=[71]))[0]
    builder = AsyncMock()
    relations = AsyncMock()
    assert await refresh.refresh_claim(factory, claimed, builder=builder, relation_builder=relations)
    builder.assert_not_called()
    relations.assert_not_called()
    async with factory() as session:
        assert (await session.get(ENTITY_READ_MODELS["game_notice"], (71, 2, "current"))).title == "JA notice"
        assert (await session.get(ReadProjectionDirty, ("game_notice", 71, "current"))).processed_generation == claimed.generation
