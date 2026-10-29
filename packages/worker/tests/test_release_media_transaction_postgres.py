"""Verify the worker's connection-bound advisory lock on an isolated PostgreSQL DB."""

import os
import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story_worker.broker import job_lock

URL = os.getenv("WUWA_VIDEO_LOCK_TEST_DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not URL, reason="Set WUWA_VIDEO_LOCK_TEST_DATABASE_URL to an isolated PostgreSQL database")
async def test_commit_ends_transaction_without_releasing_session_job_lock():
    engine = create_async_engine(URL)
    assert engine.url.database.endswith("_verification"), "Use a dedicated verification database"
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    keys = {"first": -2147483648, "second": -1 - secrets.randbelow(2147483647)}
    try:
        async with engine.connect() as connection:
            pid = await connection.scalar(text("SELECT pg_backend_pid()"))
            async with job_lock(connection, keys["first"], keys["second"]):
                async with factory(bind=connection) as session:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    await session.execute(text("SELECT 1"))
                    await session.commit()
                    async with engine.connect() as observer:
                        state = (await observer.execute(text(
                            "SELECT state, xact_start IS NULL AS no_transaction "
                            "FROM pg_stat_activity WHERE pid = :pid"
                        ), {"pid": pid})).one()
                        acquired = await observer.scalar(text(
                            "SELECT pg_try_advisory_lock(:first, :second)"
                        ), keys)
                        if acquired:
                            await observer.scalar(text(
                                "SELECT pg_advisory_unlock(:first, :second)"
                            ), keys)
                        assert (state.state, state.no_transaction, acquired) == ("idle", True, False)
    finally:
        await engine.dispose()
