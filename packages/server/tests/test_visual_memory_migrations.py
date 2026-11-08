"""Apply, roll back, and reapply this slice on a new disposable PostgreSQL DB."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_visual_migrations_preserve_game_tables_on_rollback():
    configured = os.getenv("WUWA_VISUAL_TEST_DATABASE_URL")
    if not configured:
        pytest.skip("Disposable PostgreSQL required")
    name = "visual_migration_test_" + uuid4().hex
    owner_url = make_url(configured).set(database="postgres")
    owner = create_async_engine(owner_url, isolation_level="AUTOCOMMIT")
    url = owner_url.set(database=name)
    async with owner.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(url)
    try:

        async def migrate(action, version):
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", action, version],
                cwd=Path(__file__).parents[1],
                env={**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert result.returncode == 0, f"Migration {action} {version} failed"

        await migrate("upgrade", "head")
        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT to_regclass('core.visual_identity') IS NOT NULL")
            )
            assert await connection.scalar(
                text(
                    "SELECT to_regclass('search.ix_visual_embedding_model_dimensions') IS NOT NULL"
                )
            )
            assert await connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='search.visual_embedding'::regclass AND conname='ck_visual_embedding_dimensions_match')"
                )
            )
        await engine.dispose()
        await migrate("downgrade", "0029_storage_verification_noop")
        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT to_regclass('core.visual_identity') IS NULL")
            )
            assert await connection.scalar(text("SELECT to_regclass('graph.node') IS NOT NULL"))
        await engine.dispose()
        await migrate("upgrade", "head")
    finally:
        await engine.dispose()
        async with owner.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await owner.dispose()
