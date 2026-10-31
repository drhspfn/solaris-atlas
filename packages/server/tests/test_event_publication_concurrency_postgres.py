"""0026 permits parallel storage gates without weakening registration fences."""

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_event_asset_dependencies_postgres import (
    assets as dependencies,  # noqa: F401
)
from test_event_asset_dependencies_postgres import (
    enable,
    test_backfill_gate_vs_preexisting_parent_row_lock_returns_40001_not_deadlock,  # noqa: F401
    test_new_binding_asset_publication_race_reinvalidates_after_projection_publishes,  # noqa: F401
    test_repeatable_read_staged_snapshot_cannot_bypass_activation_guard,  # noqa: F401
    test_two_source_then_asset_transactions_retry_without_shared_upgrade_deadlock,  # noqa: F401
)
from test_read_refresh_postgres import database  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.getenv("WUWA_READ_REFRESH_TEST_DATABASE_URL"),
    reason="Disposable local PostgreSQL required",
)


def migration():
    path = Path(__file__).parents[1] / "migrations/versions/0026_event_publication_concurrency.py"
    spec = importlib.util.spec_from_file_location("event_publication_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def assets(dependencies):  # noqa: F811
    factory, engine, _ = dependencies
    module = migration()
    async with engine.begin() as connection:

        def apply(sync):
            module.op = Operations(MigrationContext.configure(sync))
            module.upgrade()

        await connection.run_sync(apply)
    return factory, engine, module


async def gate_mode(connection):
    return await connection.scalar(
        text(
            "SELECT mode FROM pg_locks WHERE locktype='advisory' AND classid=705 AND objid=25 AND pid=pg_backend_pid()"
        )
    )


@pytest.mark.asyncio
async def test_storage_publishers_share_gate_and_exclude_source_until_both_commit(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as first, engine.connect() as second:
        first_tx = await first.begin()
        await first.execute(text("INSERT INTO storage.file_object(id) VALUES(700)"))
        second_tx = await second.begin()
        # Different storage tables avoid unrelated legacy cache-revision row
        # contention and demonstrate the publication gate itself is parallel.
        await asyncio.wait_for(
            second.execute(
                text(
                    "INSERT INTO storage.file_reference(id,source_path,reference_type,metadata) VALUES(701,'capture/Absent.png','client_texture','{\"download_id\":\"current\"}')"
                )
            ),
            timeout=3,
        )
        assert await gate_mode(first) == await gate_mode(second) == "ShareLock"

        async def source():
            async with engine.begin() as connection:
                await connection.execute(text("SELECT ops.refresh_event_asset_dependencies(100)"))

        with pytest.raises(DBAPIError, match="retry transaction") as error:
            await source()
        assert error.value.orig.sqlstate == "40001"
        await first_tx.commit()
        with pytest.raises(DBAPIError, match="retry transaction"):
            await source()
        await second_tx.commit()
        await source()


@pytest.mark.asyncio
async def test_storage_to_source_upgrade_retries_without_deadlock_then_reenters_storage(assets):
    _, engine, _ = assets
    await enable(assets)
    async with engine.connect() as first, engine.connect() as second:
        first_tx = await first.begin()
        await first.execute(text("INSERT INTO storage.file_object(id) VALUES(702)"))
        second_tx = await second.begin()
        await second.execute(
            text(
                "INSERT INTO storage.file_reference(id,source_path,reference_type,metadata) VALUES(703,'capture/Absent.png','client_texture','{\"download_id\":\"current\"}')"
            )
        )
        with pytest.raises(DBAPIError, match="retry transaction") as error:
            await asyncio.wait_for(
                first.execute(
                    text("UPDATE core.game_event SET banner_path='/Game/First' WHERE id=100")
                ),
                timeout=3,
            )
        assert error.value.orig.sqlstate == "40001"
        await first_tx.rollback()
        await second.execute(
            text("UPDATE core.game_event SET banner_path='/Game/Second' WHERE id=101")
        )
        await second.execute(text("INSERT INTO storage.file_object(id) VALUES(704)"))
        assert await second.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=705 AND objid=25 AND pid=pg_backend_pid() AND mode='ExclusiveLock')"
            )
        )
        await second_tx.commit()


@pytest.mark.asyncio
async def test_0026_downgrade_restores_exclusive_publication_gate(assets):
    _, engine, module = assets
    async with engine.begin() as connection:

        def down(sync):
            module.op = Operations(MigrationContext.configure(sync))
            module.downgrade()

        await connection.run_sync(down)
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO storage.file_object(id) VALUES(705)"))
        assert await gate_mode(connection) == "ExclusiveLock"
