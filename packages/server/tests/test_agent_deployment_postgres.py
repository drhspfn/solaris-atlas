"""Exercise the actual Alembic chain and deployment setup on disposable databases."""

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

from wuwa_story.agents.database import query_database
from wuwa_story.agents.reader import READER_ROLE
from wuwa_story.agents.settings import AgentSettings

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
SERVER = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Isolated PostgreSQL with CREATEDB required")


@pytest.fixture
async def deployment_database():
    name = "agent_deploy_" + uuid4().hex
    owner = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
    url = make_url(DATABASE_URL).set(database=name)
    database = create_async_engine(url)
    async with owner.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield database, url.render_as_string(hide_password=False)
    finally:
        await database.dispose()
        async with owner.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await owner.dispose()


async def command(url, *arguments, online=False, migration_url=None):
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, *arguments], cwd=SERVER,
        env={**os.environ, "DATABASE_URL": url, "MIGRATION_DATABASE_URL": migration_url or url,
             "WUWA_ONLINE_SEARCH_INDEX_ONLY": "1" if online else "0",
             "WUWA_DB_TRANSACTION_POOLING": "0"},
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["empty-database", "upgrade-0027", "empty-staged-deploy"])
async def test_deployment_prepares_reader_and_preserves_existing_data(deployment_database, mode):
    engine, url = deployment_database
    existing = mode == "upgrade-0027"
    if existing:
        await command(url, "-m", "alembic", "upgrade", "0027_agent_workspace")
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE core.agent_deploy_canary (value text NOT NULL)"))
            await connection.execute(text("INSERT INTO core.agent_deploy_canary VALUES ('preserved')"))
    elif mode == "empty-staged-deploy":
        await command(url, "-m", "alembic", "upgrade", "0022_tree_index_schema_version")
        await command(url, "-m", "alembic", "upgrade", "0023_search_body_trgm", online=True)
    await command(url, "-m", "alembic", "upgrade", "head")
    await command(url, "scripts/setup_agent_reader.py")
    settings = AgentSettings(_env_file=None, read_database_url="")
    result = await query_database(settings, "SELECT count(*) AS total FROM core.quest", bind=engine)
    assert result["rows"] == [{"total": 0}]
    async with engine.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0028_agent_reader_role"
        assert not await connection.scalar(text(
            "SELECT rolcanlogin FROM pg_roles WHERE rolname=:role"), {"role": READER_ROLE})
        if existing:
            assert await connection.scalar(text("SELECT value FROM core.agent_deploy_canary")) == "preserved"
        # A deployment at head still repairs missing table grants.
        await connection.execute(text(f"REVOKE SELECT ON core.quest FROM {READER_ROLE}"))
    await command(url, "-m", "alembic", "upgrade", "head")
    await command(url, "scripts/setup_agent_reader.py")
    async with engine.connect() as connection:
        assert await connection.scalar(text(
            "SELECT has_table_privilege(:role,'core.quest','SELECT')"), {"role": READER_ROLE})


async def test_setup_detects_separate_restricted_runtime_login(deployment_database):
    engine, url = deployment_database
    await command(url, "-m", "alembic", "upgrade", "head")
    login = "agent_runtime_" + uuid4().hex
    runtime_url = make_url(url).set(username=login, password="isolated-test-only")
    runtime = create_async_engine(runtime_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE ROLE {login} LOGIN PASSWORD 'isolated-test-only'"))
        await command(runtime_url.render_as_string(hide_password=False),
                      "scripts/setup_agent_reader.py", migration_url=url)
        result = await query_database(AgentSettings(_env_file=None, read_database_url=""),
                                      "SELECT count(*) AS total FROM core.quest", bind=runtime)
        assert result["rows"] == [{"total": 0}]
        async with runtime.connect() as connection:
            assert not await connection.scalar(text(
                "SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user"))
    finally:
        await runtime.dispose()
        async with engine.begin() as connection:
            await connection.execute(text(f"DROP ROLE IF EXISTS {login}"))
