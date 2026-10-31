"""Staged event bindings and invalidation on disposable local PostgreSQL."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_read_refresh_postgres import database  # noqa: F401
from wuwa_story.read_models import refresh
from wuwa_story.read_models.event_assets import activate, backfill

pytestmark = pytest.mark.skipif(
    not os.getenv("WUWA_READ_REFRESH_TEST_DATABASE_URL"),
    reason="Disposable local PostgreSQL required",
)


def migration():
    path = Path(__file__).parents[1] / "migrations/versions/0025_event_asset_dependencies.py"
    spec = importlib.util.spec_from_file_location("event_assets_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def assets(database):  # noqa: F811
    factory, engine, _ = database
    module = migration()
    async with engine.begin() as connection:
        for statement in (
            "ALTER TABLE core.game_event ADD COLUMN banner_path text",
            "ALTER TABLE core.game_event_occurrence ADD COLUMN banner_path text",
            "ALTER TABLE core.game_event_occurrence ADD CONSTRAINT asset_event_fk FOREIGN KEY(event_id) REFERENCES core.game_event(id) ON DELETE CASCADE",
            "ALTER TABLE storage.file_reference ADD COLUMN source_path text, ADD COLUMN source_name text",
            'INSERT INTO core.game_event(id,source_data) VALUES(100,\'{"client":{"download_id":"current","artwork":[{"path":"/Game/UI/T_Icon.T_Icon"},{"path":"UI/extra.png"}],"prefab_artwork":[{"path":"/Game/Prefab/Foo.Foo"}]}}\'),(101,\'{"client":{"download_id":"current","artwork":[{"path":"/Game/Other"}]}}\'),(102,\'{}\')',
            "UPDATE core.game_event SET banner_path='/Game/Banner.Banner' WHERE id=100",
            'INSERT INTO core.game_event_occurrence(id,event_id,game_version,source_data) VALUES(200,100,\'3.9\',\'{"client":{"download_id":"old","artwork":[{"path":"/Game/Historical"}]}}\'),(201,100,\'3.10\',\'{"client":{"download_id":"current","artwork":[{"path":"/Game/Ignored"}]}}\')',
        ):
            await connection.execute(text(statement))

        def apply(sync):
            module.op = Operations(MigrationContext.configure(sync))
            module.upgrade()

        await connection.run_sync(apply)
        await connection.execute(text("DELETE FROM ops.read_projection_dirty"))
    yield factory, engine, module


async def keys(engine, event=100):
    async with engine.connect() as connection:
        return set(
            (
                await connection.execute(
                    text(
                        "SELECT download_id,asset_key FROM ops.event_asset_dependency WHERE event_id=:event"
                    ),
                    {"event": event},
                )
            ).all()
        )


async def dirty(engine):
    async with engine.connect() as connection:
        return set(
            (
                await connection.execute(
                    text("SELECT kind,entity_id,snapshot_key FROM ops.read_projection_dirty")
                )
            ).all()
        )


async def clear(engine):
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM ops.read_projection_dirty"))


async def reference(
    engine, identity=1, path="capture/T_Icon.png", download="current", owner=None, name=None
):
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO storage.file_reference(id,file_id,owner_node_id,source_path,source_name,reference_type,metadata) VALUES(:id,:id,:owner,:path,:name,'client_texture',CAST(:metadata AS jsonb))"
            ),
            {
                "id": identity,
                "owner": owner,
                "path": path,
                "name": name,
                "metadata": json.dumps({"download_id": download}),
            },
        )


async def enable(assets):
    factory, engine, _ = assets
    result = await backfill(factory)
    assert result == {"processed": 3, "remaining": 0}
    assert await activate(factory) == {"active": True}
    await clear(engine)


@pytest.mark.asyncio
async def test_staged_backfill_activation_and_effective_historical_banner(assets):
    factory, engine, _ = assets
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM ops.event_asset_coverage")) == 0
    with pytest.raises(ValueError, match="coverage incomplete"):
        await activate(factory)
    await reference(engine)
    existing_work = await dirty(engine)
    assert await backfill(factory, max_events=1) == {"processed": 1, "remaining": 2}
    with pytest.raises(ValueError, match="coverage incomplete"):
        await activate(factory)
    assert await backfill(factory) == {"processed": 2, "remaining": 0}
    assert await backfill(factory) == {"processed": 0, "remaining": 0}
    async with engine.connect() as connection:
        hashed = {
            name: await connection.scalar(text("SELECT ops.event_asset_key(:path)"), {"path": name})
            for name in ("T_Icon", "extra", "Foo", "Banner", "Historical", "Ignored")
        }
        assert (
            await connection.scalar(
                text("SELECT covered FROM ops.event_asset_coverage WHERE event_id=102")
            )
            is True
        )
    bindings = await keys(engine)
    assert ("old", hashed["Banner"]) in bindings
    assert ("old", hashed["Historical"]) in bindings
    assert all(
        ("current", hashed[name]) in bindings for name in ("T_Icon", "extra", "Foo", "Banner")
    )
    assert ("current", hashed["Ignored"]) not in bindings
    assert await keys(engine, 102) == set()
    await activate(factory)
    assert await dirty(engine) == existing_work
    await clear(engine)
    # Publication is not required: an asset missing at backfill time still dirties its owner.
    await reference(engine, identity=2)
    assert await dirty(engine) == {("game_event", 100, "current")}


@pytest.mark.asyncio
async def test_coarse_before_activation_then_narrow_update_delete_and_owned_assets(assets):
    factory, engine, _ = assets
    await reference(engine)
    assert ("game_event", 0, "download:current") in await dirty(engine)
    await enable(assets)
    await reference(engine, identity=2, owner=7)
    assert await dirty(engine) == {("character", 7, "current"), ("game_event", 100, "current")}
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE storage.file_reference SET source_path='capture/Other.png' WHERE id=2")
        )
    assert await dirty(engine) == {
        ("character", 7, "current"),
        ("game_event", 100, "current"),
        ("game_event", 101, "current"),
    }
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM storage.file_reference WHERE id=2"))
    assert await dirty(engine) == {("character", 7, "current"), ("game_event", 101, "current")}
    await clear(engine)
    await reference(engine, identity=3, path="capture/Unknown.png")
    assert await dirty(engine) == set()


@pytest.mark.asyncio
async def test_source_updates_rebuild_fallback_pairs_delete_and_rollback(assets):
    _, engine, _ = assets
    await enable(assets)
    old = await keys(engine)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(
            text("UPDATE core.game_event SET banner_path='/Game/Temporary' WHERE id=100")
        )
        await transaction.rollback()
    assert await keys(engine) == old
    assert await dirty(engine) == set()
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE core.game_event SET banner_path='/Game/NewBanner' WHERE id=100")
        )
    assert not any(
        kind == "game_event" and identity == 0 for kind, identity, _ in await dirty(engine)
    )
    async with engine.connect() as connection:
        new_key = await connection.scalar(text("SELECT ops.event_asset_key('/Game/NewBanner')"))
    assert ("old", new_key) in await keys(engine)
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM core.game_event_occurrence WHERE id=200"))
    assert not any(download == "old" for download, _ in await keys(engine))
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM core.game_event WHERE id=100"))
    assert await keys(engine) == set()
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM ops.event_asset_coverage WHERE event_id=100")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_normalization_wildcards_names_notice_fallback_and_history(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO ops.read_projection_scope(kind,snapshot_key) VALUES('game_event','3.9.0'),('game_event','3.10.0')"
            )
        )
        normalized = (
            await connection.execute(
                text(
                    "SELECT ops.event_asset_key('/Game/UI/T_Icon.T_Icon'),ops.event_asset_key('capture\\T_ICON.PNG'),ops.event_asset_key('Texture2D''/Game/UI/T_Icon.T_Icon''')"
                )
            )
        ).one()
        assert len(set(normalized)) == 1
    await reference(engine, path="capture/Banner.png", download="old")
    assert await dirty(engine) == {
        ("game_event", 100, "current"),
        ("game_event", 100, "3.9.0"),
        ("game_event", 100, "3.10.0"),
    }
    await clear(engine)
    await reference(engine, identity=2, path="capture/exported.png", name="T_Icon")
    assert ("game_event", 100, "current") in await dirty(engine)
    await clear(engine)
    await reference(engine, identity=3, path="/")
    assert ("game_event", 0, "download:current") in await dirty(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE core.game_event SET source_data=jsonb_set(source_data,'{client,artwork}','[{\"path\":\"/\"}]') WHERE id=101"
            )
        )
    await clear(engine)
    await reference(engine, identity=4, path="capture/Unrelated.png")
    assert ("game_event", 101, "current") in await dirty(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO storage.file_reference(id,reference_type,source_path,metadata) VALUES(6,'notice_image','capture/notice.png','{\"download_id\":\"current\"}')"
            )
        )
    assert ("game_event", 0, "download:current") in await dirty(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO storage.file_reference(id,reference_type) VALUES(5,'notice_image')")
        )
    assert {("game_notice", 0, "current"), ("game_event", 0, "current")} <= await dirty(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", ["update_then_backfill", "backfill_then_update", "backfill_then_delete"]
)
async def test_backfill_serializes_with_source_writes_and_deletion(assets, action):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as first:
        transaction = await first.begin()
        if action == "update_then_backfill":
            await first.execute(
                text("UPDATE core.game_event SET banner_path='/Game/Concurrent' WHERE id=100")
            )
            statement = "SELECT ops.refresh_event_asset_dependencies(100)"
        else:
            await first.execute(text("SELECT ops.refresh_event_asset_dependencies(100)"))
            statement = (
                "DELETE FROM core.game_event WHERE id=100"
                if action.endswith("delete")
                else "UPDATE core.game_event SET banner_path='/Game/Concurrent' WHERE id=100"
            )

        async def competing():
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL statement_timeout='5s'"))
                await connection.execute(text(statement))

        with pytest.raises(DBAPIError, match="retry transaction") as error:
            await competing()
        assert error.value.orig.sqlstate == "40001"
        await transaction.commit()
        await asyncio.wait_for(competing(), timeout=5)
    if action.endswith("delete"):
        assert await keys(engine) == set()
    else:
        async with engine.connect() as connection:
            value = await connection.scalar(text("SELECT ops.event_asset_key('/Game/Concurrent')"))
        assert ("old", value) in await keys(engine)


@pytest.mark.asyncio
async def test_downgrade_restores_coarse_storage_and_source_invalidations(assets):
    _, engine, module = assets
    await enable(assets)
    async with engine.begin() as connection:

        def down(sync):
            module.op = Operations(MigrationContext.configure(sync))
            module.downgrade()

        await connection.run_sync(down)
        assert (
            await connection.scalar(text("SELECT to_regclass('ops.event_asset_dependency')"))
            is None
        )
    await clear(engine)
    await reference(engine)
    assert ("game_event", 0, "download:current") in await dirty(engine)

    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE core.game_event SET banner_path='/Game/AfterRollback' WHERE id=100")
        )
    assert ("game_event", 0, "download:current") in await dirty(engine)


@pytest.mark.asyncio
async def test_database_activation_guard_and_new_zero_asset_source_after_activation(assets):
    factory, engine, _ = assets
    with pytest.raises(DBAPIError, match="coverage incomplete"):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE ops.event_asset_state SET active=true WHERE id=1")
            )
    await backfill(factory)
    await activate(factory)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO core.game_event(id,source_data) VALUES(103,'{}')")
        )
        assert (
            await connection.scalar(
                text("SELECT covered FROM ops.event_asset_coverage WHERE event_id=103")
            )
            is True
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM ops.event_asset_dependency WHERE event_id=103")
            )
            == 0
        )
    assert not any(
        identity == 0 and snapshot.startswith("download:")
        for _, identity, snapshot in await dirty(engine)
    )


@pytest.mark.asyncio
async def test_old_new_downloads_locations_and_occurrence_reassignment(assets):
    _, engine, _ = assets
    await enable(assets)
    await reference(engine, identity=1, path="capture/Banner.png", download="old")
    await reference(engine, identity=2, path="capture/Other.png")
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                'UPDATE storage.file_reference SET metadata=\'{"download_id":"current"}\' WHERE id=1'
            )
        )
    assert await dirty(engine) == {("game_event", 100, "current")}
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO storage.file_location(id,file_id,available) VALUES(1,1,true)")
        )
    assert await dirty(engine) == {("game_event", 100, "current")}
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE storage.file_location SET file_id=2 WHERE id=1"))
    assert await dirty(engine) == {("game_event", 100, "current"), ("game_event", 101, "current")}
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM storage.file_location WHERE id=1"))
    assert await dirty(engine) == {("game_event", 101, "current")}
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE core.game_event_occurrence SET event_id=101 WHERE id=200")
        )
    assert not any(download == "old" for download, _ in await keys(engine, 100))
    async with engine.connect() as connection:
        historical = await connection.scalar(text("SELECT ops.event_asset_key('/Game/Historical')"))
    assert ("old", historical) in await keys(engine, 101)


@pytest.mark.asyncio
async def test_backfill_deadline_rolls_back_one_event_and_preserves_completed_progress(assets):
    factory, engine, _ = assets
    async with engine.begin() as connection:
        await connection.execute(
            text("""CREATE FUNCTION ops.delay_test_coverage() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.event_id=101 THEN PERFORM pg_sleep(1.5); END IF; RETURN NEW; END $$""")
        )
        await connection.execute(
            text(
                "CREATE TRIGGER delay_test_coverage BEFORE INSERT ON ops.event_asset_coverage FOR EACH ROW EXECUTE FUNCTION ops.delay_test_coverage()"
            )
        )
    with pytest.raises(DBAPIError, match="statement timeout"):
        await backfill(factory, max_events=2, timeout_seconds=1)
    async with engine.begin() as connection:
        assert list(
            await connection.scalars(
                text(
                    "SELECT event_id FROM ops.event_asset_coverage WHERE covered ORDER BY event_id"
                )
            )
        ) == [100]
        await connection.execute(
            text("DROP TRIGGER delay_test_coverage ON ops.event_asset_coverage")
        )
    assert await backfill(factory) == {"processed": 2, "remaining": 0}


@pytest.mark.asyncio
async def test_repeatable_read_backfill_cannot_restore_an_old_binding_snapshot(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as stale:
        await stale.execution_options(isolation_level="REPEATABLE READ")
        transaction = await stale.begin()
        await stale.execute(text("SELECT * FROM ops.event_asset_coverage WHERE event_id=100"))
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE core.game_event SET banner_path='/Game/Newest' WHERE id=100")
            )
        with pytest.raises(DBAPIError, match="concurrent update|serialize"):
            await stale.execute(text("SELECT ops.refresh_event_asset_dependencies(100)"))
        await transaction.rollback()
    async with engine.connect() as connection:
        value = await connection.scalar(text("SELECT ops.event_asset_key('/Game/Newest')"))
    assert ("old", value) in await keys(engine)


@pytest.mark.asyncio
async def test_activation_waits_for_inflight_new_event_and_its_coverage(assets):
    factory, engine, _ = assets
    await backfill(factory)
    async with engine.connect() as writer:
        transaction = await writer.begin()
        await writer.execute(text("INSERT INTO core.game_event(id,source_data) VALUES(103,'{}')"))
        task = asyncio.create_task(activate(factory))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
        await transaction.commit()
        assert await asyncio.wait_for(task, timeout=5) == {"active": True}
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT covered FROM ops.event_asset_coverage WHERE event_id=103")
            )
            is True
        )


@pytest.mark.asyncio
async def test_asset_publication_still_fences_an_inflight_entity_refresh(assets):
    factory, engine, _ = assets
    await enable(assets)
    await reference(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE ops.read_projection_dirty SET available_at=clock_timestamp()")
        )
    claims = await refresh.claim_refreshes(
        factory, kinds=("game_event",), entity_ids=[100], snapshot_key="current", limit=1
    )
    assert len(claims) == 1
    await reference(engine, identity=2)
    entry = {
        "entity_id": 100,
        "locale_id": 1,
        "snapshot_key": "current",
        "title": "Event",
        "sort_title": "event",
        "card": {},
        "searchable_text": "Event",
        "payload": {},
    }
    assert await refresh.publish_refresh(factory, claims[0], [entry]) is False
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT requested_generation>processed_generation FROM ops.read_projection_dirty WHERE kind='game_event' AND entity_id=100 AND snapshot_key='current'"
                )
            )
            is True
        )


@pytest.mark.asyncio
async def test_blank_banner_fallback_and_negative_asset_lookup_after_new_binding(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE core.game_event_occurrence SET banner_path='' WHERE id=200")
        )
        await connection.execute(
            text(
                'UPDATE core.game_event SET banner_path=\'\',source_data=\'{"client":{"download_id":"current"}}\' WHERE id=102'
            )
        )
        await connection.execute(
            text(
                'INSERT INTO core.game_event_occurrence(id,event_id,banner_path,source_data) VALUES(202,102,\'\',\'{"client":{"download_id":"old"}}\')'
            )
        )
        banner = await connection.scalar(text("SELECT ops.event_asset_key('/Game/Banner.Banner')"))
    assert ("old", banner) in await keys(engine)
    assert not any(key == "*" for _, key in await keys(engine))
    assert await keys(engine, 102) == set()
    await clear(engine)
    await reference(engine, path="capture/NewAsset.png")
    assert await dirty(engine) == set()
    # A previous negative lookup is not permanent when a source gains an asset.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                'UPDATE core.game_event SET source_data=\'{"client":{"download_id":"current","artwork":[{"path":"/Game/NewAsset"}]}}\' WHERE id=102'
            )
        )
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE storage.file_reference SET metadata=metadata WHERE id=1")
        )
    assert await dirty(engine) == {("game_event", 102, "current")}


@pytest.mark.asyncio
async def test_owned_blank_audio_paths_do_not_queue_download_bulk_but_wildcards_remain(assets):
    _, engine, _ = assets
    await enable(assets)
    for identity, path in enumerate((None, "", "   ", "/"), start=10):
        await clear(engine)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO storage.file_reference(id,owner_node_id,source_path,source_name,reference_type,metadata) VALUES(:id,7,:path,'en','character_voice','{\"download_id\":\"current\"}')"
                ),
                {"id": identity, "path": path},
            )
        assert await dirty(engine) == {("character", 7, "current")}
    for identity, path in enumerate((None, "", "   "), start=20):
        await clear(engine)
        await reference(engine, identity=identity, path=path)
        assert await dirty(engine) == set()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                'UPDATE core.game_event SET source_data=\'{"client":{"download_id":"current","artwork":[{"path":"/"}]}}\' WHERE id=102'
            )
        )
    await clear(engine)
    await reference(engine, identity=30, owner=7, path=None)
    assert await dirty(engine) == {("character", 7, "current"), ("game_event", 102, "current")}
    await clear(engine)
    await reference(engine, identity=31, path="/")
    assert ("game_event", 0, "download:current") in await dirty(engine)

    # Notice-image fallback remains exactly as before, including a null path.
    await clear(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO storage.file_reference(id,reference_type,metadata) VALUES(32,'notice_image','{\"download_id\":\"current\"}')"
            )
        )
    assert ("game_event", 0, "download:current") in await dirty(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_path,source_name", [("capture/NewRacing.png", None), (None, "NewRacing"), (None, None)]
)
async def test_new_binding_asset_publication_race_reinvalidates_after_projection_publishes(
    assets, source_path, source_name
):
    factory, engine, _ = assets
    await enable(assets)
    # Test-only gate permits a projection to publish after source commit but
    # before asset invalidation/commit, reproducing the reported interleaving.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER FUNCTION ops.mark_event_asset_changed(bigint,text) RENAME TO mark_event_asset_changed_original"
            )
        )
        await connection.execute(
            text("""CREATE FUNCTION ops.mark_event_asset_changed(identity bigint, version text DEFAULT NULL)
            RETURNS void LANGUAGE plpgsql AS $$ BEGIN
                IF current_setting('test.pause_asset_mark',true)='on' THEN
                    PERFORM pg_advisory_xact_lock(705,26);
                END IF;
                PERFORM ops.mark_event_asset_changed_original(identity,version);
            END $$""")
        )
    async with engine.connect() as gate, engine.connect() as source, engine.connect() as asset:
        gate_tx = await gate.begin()
        await gate.execute(text("SELECT pg_advisory_xact_lock(705,26)"))
        source_tx = await source.begin()
        await source.execute(
            text(
                "UPDATE core.game_event SET source_data=jsonb_set(source_data,'{client,artwork}',CAST(:artwork AS jsonb)) WHERE id=100"
            ),
            {
                "artwork": json.dumps(
                    [
                        {
                            "path": "/"
                            if source_path is None and source_name is None
                            else "/Game/NewRacing"
                        }
                    ]
                )
            },
        )
        token = uuid4()
        generation = await source.scalar(
            text(
                "UPDATE ops.read_projection_dirty SET lease_token=:token,lease_until=clock_timestamp()+interval '1 minute' WHERE kind='game_event' AND entity_id=100 AND snapshot_key='current' RETURNING requested_generation"
            ),
            {"token": token},
        )
        claim = refresh.RefreshClaim("game_event", 100, "current", generation, token, 0)
        asset_tx = await asset.begin()
        await asset.execute(text("SET LOCAL test.pause_asset_mark='on'"))
        insert = asyncio.create_task(
            asset.execute(
                text(
                    "INSERT INTO storage.file_reference(id,source_path,source_name,reference_type,metadata) VALUES(90,:path,:name,'client_texture','{\"download_id\":\"current\"}')"
                ),
                {"path": source_path, "name": source_name},
            )
        )
        try:
            with pytest.raises(DBAPIError, match="retry transaction") as error:
                await insert
            assert error.value.orig.sqlstate == "40001"
            await asset_tx.rollback()
            await source_tx.commit()
            asset_tx = await asset.begin()
            await asset.execute(text("SET LOCAL test.pause_asset_mark='on'"))
            insert = asyncio.create_task(
                asset.execute(
                    text(
                        "INSERT INTO storage.file_reference(id,source_path,source_name,reference_type,metadata) VALUES(90,:path,:name,'client_texture','{\"download_id\":\"current\"}')"
                    ),
                    {"path": source_path, "name": source_name},
                )
            )
            # Asset cannot invalidate before the test gate is released. Publish
            # the source generation while the asset is still uncommitted.
            entry = {
                "entity_id": 100,
                "locale_id": 1,
                "snapshot_key": "current",
                "title": "Event",
                "sort_title": "event",
                "card": {},
                "searchable_text": "Event",
                "payload": {"asset_url": None},
            }
            assert (
                await asyncio.wait_for(refresh.publish_refresh(factory, claim, [entry]), timeout=3)
                is True
            )
            await gate_tx.commit()
            await asyncio.wait_for(insert, timeout=5)
            await asset_tx.commit()
        finally:
            if gate_tx.is_active:
                await gate_tx.rollback()
            if source_tx.is_active:
                await source_tx.rollback()
            if not insert.done():
                insert.cancel()
                await asyncio.gather(insert, return_exceptions=True)
            if asset_tx.is_active:
                await asset_tx.rollback()
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT requested_generation,processed_generation FROM ops.read_projection_dirty WHERE kind='game_event' AND entity_id=100 AND snapshot_key='current'"
                )
            )
        ).one()
        assert row.requested_generation > row.processed_generation == generation
        assert (
            await connection.scalar(text("SELECT count(*) FROM storage.file_reference WHERE id=90"))
            == 1
        )


@pytest.mark.asyncio
async def test_source_registration_and_blank_asset_gate_conflicts_are_retryable(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as first:
        transaction = await first.begin()
        await first.execute(text("SELECT ops.refresh_event_asset_dependencies(100)"))

        async def other_source():
            async with engine.begin() as connection:
                await connection.execute(text("SELECT ops.refresh_event_asset_dependencies(101)"))

        with pytest.raises(DBAPIError, match="retry transaction"):
            await other_source()
        with pytest.raises(DBAPIError, match="retry transaction"):
            await reference(engine, identity=80, owner=7, path=None)
        await transaction.commit()
    await other_source()
    await reference(engine, identity=80, owner=7, path=None)


@pytest.mark.asyncio
async def test_relevant_asset_repeatable_read_publication_fails_closed(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        transaction = await connection.begin()
        with pytest.raises(DBAPIError, match="READ COMMITTED"):
            await connection.execute(
                text(
                    "INSERT INTO storage.file_reference(id,source_path,reference_type,metadata) VALUES(81,'capture/T_Icon.png','client_texture','{\"download_id\":\"current\"}')"
                )
            )
        await transaction.rollback()
    assert await dirty(engine) == set()


@pytest.mark.asyncio
async def test_asset_first_blocks_source_registration_before_source_row_locks(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as asset, engine.connect() as source:
        asset_tx = await asset.begin()
        await asset.execute(
            text(
                "INSERT INTO storage.file_reference(id,source_path,reference_type,metadata) VALUES(82,'capture/NewLater.png','client_texture','{\"download_id\":\"current\"}')"
            )
        )
        source_tx = await source.begin()
        update = asyncio.create_task(
            source.execute(
                text(
                    "UPDATE core.game_event SET source_data=jsonb_set(source_data,'{client,artwork}','[{\"path\":\"/Game/NewLater\"}]') WHERE id=100"
                )
            )
        )
        try:
            with pytest.raises(DBAPIError, match="retry transaction"):
                await update
            await source_tx.rollback()
            # The retryable BEFORE STATEMENT failure did not take source rows.
            async with engine.begin() as probe:
                await probe.execute(
                    text("SELECT id FROM core.game_event WHERE id=100 FOR UPDATE NOWAIT")
                )
            await asset_tx.commit()
            source_tx = await source.begin()
            await source.execute(
                text(
                    "UPDATE core.game_event SET source_data=jsonb_set(source_data,'{client,artwork}','[{\"path\":\"/Game/NewLater\"}]') WHERE id=100"
                )
            )
            await source_tx.commit()
        finally:
            if asset_tx.is_active:
                await asset_tx.rollback()
            if not update.done():
                update.cancel()
                await asyncio.gather(update, return_exceptions=True)
            if source_tx.is_active:
                await source_tx.rollback()
    assert ("game_event", 100, "current") in await dirty(engine)


@pytest.mark.asyncio
async def test_repeatable_read_staged_snapshot_cannot_bypass_activation_guard(assets):
    factory, engine, _ = assets
    await backfill(factory)
    await clear(engine)
    async with engine.connect() as stale:
        await stale.execution_options(isolation_level="REPEATABLE READ")
        transaction = await stale.begin()
        assert (
            await stale.scalar(text("SELECT active FROM ops.event_asset_state WHERE id=1")) is False
        )
        await activate(factory)
        with pytest.raises(DBAPIError, match="READ COMMITTED") as error:
            await stale.execute(
                text(
                    "INSERT INTO storage.file_reference(id,owner_node_id,source_path,reference_type,metadata) VALUES(85,7,'capture/T_Icon.png','client_texture','{\"download_id\":\"current\"}')"
                )
            )
        assert error.value.orig.sqlstate == "40001"
        await transaction.rollback()
    assert await dirty(engine) == set()


@pytest.mark.asyncio
async def test_activation_gate_does_not_invert_source_relation_locks(assets):
    factory, engine, _ = assets
    await backfill(factory)
    async with engine.connect() as source:
        transaction = await source.begin()
        await source.execute(text("LOCK TABLE core.game_event IN ROW EXCLUSIVE MODE"))
        assert await asyncio.wait_for(activate(factory), timeout=2) == {"active": True}
        await source.execute(
            text("UPDATE core.game_event SET banner_path='/Game/AfterActivation' WHERE id=100")
        )
        await transaction.commit()


@pytest.mark.asyncio
async def test_backfill_gate_vs_preexisting_parent_row_lock_returns_40001_not_deadlock(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as caller:
        transaction = await caller.begin()
        await caller.execute(text("SELECT id FROM core.game_event WHERE id=100 FOR UPDATE"))

        async def rebuild():
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL statement_timeout='5s'"))
                await connection.execute(text("SELECT ops.refresh_event_asset_dependencies(100)"))

        task = asyncio.create_task(rebuild())
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(DBAPIError, match="retry transaction") as error:
                await caller.execute(
                    text("UPDATE core.game_event SET banner_path='/Game/Contended' WHERE id=100")
                )
            assert error.value.orig.sqlstate == "40001"
        finally:
            await transaction.rollback()
        await asyncio.wait_for(task, timeout=5)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE core.game_event SET banner_path='/Game/Retried' WHERE id=100")
        )
        value = await connection.scalar(text("SELECT ops.event_asset_key('/Game/Retried')"))
    assert ("old", value) in await keys(engine)


@pytest.mark.asyncio
async def test_two_source_then_asset_transactions_retry_without_shared_upgrade_deadlock(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as first:
        transaction = await first.begin()
        await first.execute(
            text("UPDATE core.game_event SET banner_path='/Game/First' WHERE id=100")
        )

        async def second():
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE core.game_event SET banner_path='/Game/Second' WHERE id=101")
                )
                await connection.execute(
                    text(
                        "INSERT INTO storage.file_reference(id,source_path,reference_type,metadata) VALUES(87,'capture/Other.png','client_texture','{\"download_id\":\"current\"}')"
                    )
                )

        with pytest.raises(DBAPIError, match="retry transaction") as error:
            await asyncio.wait_for(second(), timeout=2)
        assert error.value.orig.sqlstate == "40001"
        await first.execute(
            text(
                "INSERT INTO storage.file_reference(id,source_path,reference_type,metadata) VALUES(86,'capture/T_Icon.png','client_texture','{\"download_id\":\"current\"}')"
            )
        )
        await transaction.commit()
    await asyncio.wait_for(second(), timeout=2)
    assert {("game_event", 100, "current"), ("game_event", 101, "current")} <= await dirty(engine)
