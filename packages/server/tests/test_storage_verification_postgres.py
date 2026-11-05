"""Exercise the actual statement trigger on disposable PostgreSQL databases."""

import importlib.util
import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_event_asset_dependencies_postgres import assets  # noqa: F401
from test_read_refresh_postgres import database  # noqa: F401
from test_storage_verification_migration import migration

pytestmark = pytest.mark.skipif(
    not os.getenv("WUWA_READ_REFRESH_TEST_DATABASE_URL"), reason="Disposable PostgreSQL required",
)


@pytest_asyncio.fixture
async def verified_assets(assets):  # noqa: F811
    _, engine, _ = assets
    path = Path(__file__).parents[1] / "migrations/versions/0026_event_publication_concurrency.py"
    spec = importlib.util.spec_from_file_location("publication_locks", path)
    locks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(locks)
    module = migration()
    async with engine.begin() as connection:
        def apply(sync):
            operations = Operations(MigrationContext.configure(sync))
            locks.op = operations
            locks.upgrade()
            module.op = operations
            module.upgrade()
        await connection.run_sync(apply)
        for statement in (
            "ALTER TABLE storage.file_location ADD COLUMN verified_at timestamptz, ADD COLUMN etag text",
            "INSERT INTO storage.file_object(id) VALUES(501),(502)",
            "INSERT INTO storage.file_reference(id,owner_node_id,file_id,reference_type) VALUES(501,7,501,'character_image'),(502,8,502,'character_image')",
            "INSERT INTO storage.file_location(id,file_id,object_key,verified_at,etag) VALUES(501,501,'one','2026-01-01','a'),(502,502,'two','2026-01-01','b')",
            "DELETE FROM ops.read_projection_dirty",
        ):
            await connection.execute(text(statement))
    yield engine, module


@pytest.mark.asyncio
@pytest.mark.parametrize(("statement", "invalidates"), [
    ("UPDATE storage.file_location SET verified_at=now() WHERE id=501", False),
    ("UPDATE storage.file_location SET available=available WHERE id=501", False),
    ("UPDATE storage.file_location SET verified_at=now() WHERE id=-1", False),
    ("UPDATE storage.file_location SET available=false WHERE id=501", True),
    ("UPDATE storage.file_location SET etag='changed' WHERE id=501", True),
    ("UPDATE storage.file_location SET object_key='changed' WHERE id=501", True),
    ("UPDATE storage.file_location SET verified_at=now(),available=CASE WHEN id=502 THEN false ELSE available END", True),
    ("DELETE FROM storage.file_location WHERE id=501", True),
    ("INSERT INTO storage.file_location(id,file_id,object_key) VALUES(503,501,'extra')", True),
])
async def test_only_verification_noops_avoid_invalidation(verified_assets, statement, invalidates):
    engine, _ = verified_assets
    async with engine.begin() as connection:
        await connection.execute(text(statement))
        count = await connection.scalar(text("SELECT count(*) FROM ops.read_projection_dirty"))
        assert bool(count) is invalidates


@pytest.mark.asyncio
async def test_downgrade_restores_timestamp_invalidation(verified_assets):
    engine, module = verified_assets
    async with engine.begin() as connection:
        def revert(sync):
            module.op = Operations(MigrationContext.configure(sync))
            module.downgrade()
        await connection.run_sync(revert)
        await connection.execute(text("UPDATE storage.file_location SET verified_at=now() WHERE id=501"))
        assert await connection.scalar(text("SELECT count(*) FROM ops.read_projection_dirty"))


@pytest.mark.asyncio
async def test_verification_noop_still_requires_read_committed(verified_assets):
    engine, _ = verified_assets
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="REPEATABLE READ")
        with pytest.raises(DBAPIError):
            await connection.execute(text("UPDATE storage.file_location SET verified_at=now() WHERE id=501"))
        await connection.rollback()
