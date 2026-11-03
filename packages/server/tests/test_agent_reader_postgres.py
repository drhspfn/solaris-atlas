import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.agents.database import query_database
from wuwa_story.agents.reader import READER_ROLE, provision_reader, reader_connection
from wuwa_story.agents.settings import AgentSettings

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="Isolated PostgreSQL required")


async def test_managed_reader_is_passwordless_idempotent_and_rejects_writes():
    engine = create_async_engine(DATABASE_URL)
    settings = AgentSettings(_env_file=None)
    try:
        for _ in range(2):
            result = await query_database(settings, "SELECT count(*) AS total FROM core.quest", bind=engine)
            assert result["rows"][0]["total"] >= 0
        async with reader_connection(settings, engine) as connection:
            assert await connection.scalar(text("SELECT current_user")) == READER_ROLE
            assert await connection.scalar(text("SHOW transaction_read_only")) == "on"
            assert not await connection.scalar(text(
                "SELECT has_schema_privilege(current_user,'auth','USAGE')"))
            with pytest.raises(DBAPIError):
                await connection.execute(text("DELETE FROM core.quest WHERE false"))
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT current_user")) != READER_ROLE
            assert not await connection.scalar(text(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname=:role"), {"role": READER_ROLE})
    finally:
        await engine.dispose()


async def test_reader_works_for_runtime_login_without_create_role():
    engine = create_async_engine(DATABASE_URL)
    settings = AgentSettings(_env_file=None)
    login = "agent_test_" + uuid4().hex
    runtime = create_async_engine(engine.url.set(
        username=login, password="isolated-test-only"))
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE ROLE {login} LOGIN PASSWORD 'isolated-test-only'"))
            await provision_reader(connection, settings, application_role=login)
        # Runtime can verify/reuse correct grants without CREATEROLE, ownership or a reader password.
        result = await query_database(settings, "SELECT count(*) AS total FROM core.quest", bind=runtime)
        assert result["rows"][0]["total"] >= 0
        async with runtime.connect() as connection:
            assert not await connection.scalar(text(
                "SELECT has_schema_privilege(current_user,'core','USAGE')"))
    finally:
        await runtime.dispose()
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP ROLE IF EXISTS {login}"))
        await engine.dispose()


async def test_unsafe_grant_drift_fails_closed_until_repaired():
    engine = create_async_engine(DATABASE_URL)
    settings = AgentSettings(_env_file=None)
    try:
        async with engine.begin() as connection:
            await provision_reader(connection, settings)
            await connection.execute(text(f"GRANT INSERT ON core.quest TO {READER_ROLE}"))
        with pytest.raises(ValueError, match="privileged or write"):
            await query_database(settings, "SELECT count(*) FROM core.quest", bind=engine)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"REVOKE INSERT ON core.quest FROM {READER_ROLE}"))
        await engine.dispose()


async def test_missing_source_grant_is_automatically_restored():
    engine = create_async_engine(DATABASE_URL)
    settings = AgentSettings(_env_file=None)
    try:
        async with engine.begin() as connection:
            await provision_reader(connection, settings)
            await connection.execute(text(f"REVOKE SELECT ON core.quest FROM {READER_ROLE}"))
        result = await query_database(settings, "SELECT count(*) AS total FROM core.quest", bind=engine)
        assert result["rows"][0]["total"] >= 0
    finally:
        await engine.dispose()
