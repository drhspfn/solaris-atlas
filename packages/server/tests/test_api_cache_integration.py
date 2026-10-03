import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.api.cache import REVISION_SQL, PublicResponseCache
from wuwa_story.config.settings import Settings

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
REDIS_URL = os.getenv("WUWA_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL or not REDIS_URL,
                              reason="Isolated PostgreSQL and Redis test URLs are required")


@pytest.mark.asyncio
async def test_commit_rollback_and_real_redis_invalidation():
    engine = create_async_engine(DATABASE_URL)
    redis = Redis.from_url(REDIS_URL)
    calls = 0

    async def revision():
        async with engine.connect() as db:
            return str(await db.scalar(REVISION_SQL))

    app = FastAPI()
    @app.get("/catalog")
    async def catalog():
        nonlocal calls
        calls += 1
        async with engine.connect() as db:
            return {"count": await db.scalar(text("SELECT count(*) FROM ops.game_release"))}

    app.add_middleware(PublicResponseCache, redis=redis, revision_reader=revision,
                       settings=Settings(_env_file=None, api_cache_namespace=uuid4().hex))
    inserted = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            initial = await revision()
            baseline = (await client.get("/catalog")).json()["count"]
            assert (await client.get("/catalog")).headers["x-api-cache"] == "HIT"
            async with engine.connect() as writer:
                tx = await writer.begin()
                await writer.execute(text("""
                    INSERT INTO ops.game_release (sequence, game_version, upstream_name)
                    VALUES (700001, 'cache-test-rollback', 'test')
                """))
                assert await revision() == initial
                await tx.rollback()
            assert await revision() == initial
            assert (await client.get("/catalog")).headers["x-api-cache"] == "HIT"
            async with engine.begin() as writer:
                inserted = await writer.scalar(text("""
                    INSERT INTO ops.game_release (sequence, game_version, upstream_name)
                    VALUES (700002, 'cache-test-commit', 'test') RETURNING id
                """))
            assert await revision() != initial
            result = await client.get("/catalog")
            assert result.headers["x-api-cache"] == "MISS"
            assert result.json()["count"] == baseline + 1
            assert (await client.get("/catalog")).headers["x-api-cache"] == "HIT"
            assert calls == 2
    finally:
        if inserted:
            async with engine.begin() as db:
                await db.execute(text("DELETE FROM ops.game_release WHERE id=:id"), {"id": inserted})
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_trigger_coverage_bulk_writes_truncate_and_migration_roundtrip():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as db:
            tx = await db.begin()
            watched = await db.scalar(text("SELECT count(*) FROM ops.public_cache_revision"))
            assert watched > 40
            assert await db.scalar(text("""
                SELECT count(*) FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE t.tgname='public_cache_changed' AND n.nspname='auth'
            """)) == 0
            before = await db.scalar(REVISION_SQL)
            await db.execute(text("""
                INSERT INTO ops.game_release (sequence, game_version, upstream_name)
                SELECT 710000 + i, 'cache-bulk-' || i, 'test' FROM generate_series(1, 1000) i
            """))
            after = await db.scalar(REVISION_SQL)
            assert before != after
            await db.execute(text("UPDATE ops.game_release SET upstream_name='same-transaction' WHERE sequence>=710000"))
            assert await db.scalar(REVISION_SQL) == after  # Once per table/transaction.
            await db.execute(text("TRUNCATE search.dialogue_chunk_embedding"))
            assert await db.scalar(REVISION_SQL) != after

            path = Path(__file__).parents[1] / "migrations/versions/0008_public_api_cache.py"
            spec = importlib.util.spec_from_file_location("cache_migration", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            def roundtrip(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    module.downgrade()
                    assert connection.scalar(text("SELECT to_regclass('ops.public_cache_revision')")) is None
                    module.upgrade()
                    assert connection.scalar(text("SELECT count(*) FROM ops.public_cache_revision")) == watched
            await db.run_sync(roundtrip)
            await tx.rollback()
    finally:
        await engine.dispose()
