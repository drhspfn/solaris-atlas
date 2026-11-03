from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, Integer, Table, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents import audit
from wuwa_story.agents.database import read_schema, validate_sql
from wuwa_story.agents.embeddings import embedding_request, embedding_settings, model_key
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.agents.workspace import MemoryWrite, save_memory
from wuwa_story.db.base import Base
from wuwa_story.db.models.agent_workspace import AgentToolAudit


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM core.quest",
        "SELECT * INTO core.stolen FROM core.quest",
        "SELECT * FROM auth.app_user",
        "SELECT * FROM ops.agent_call",
        "SELECT pg_sleep(10)",
        "SELECT CAST('payload' AS public.custom_type)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM core.quest FOR UPDATE",
        "WITH x AS (DELETE FROM core.quest RETURNING *) SELECT * FROM x",
        "SELECT * FROM core.quest; SELECT * FROM core.character",
        "SELECT public.count(node_id) FROM core.quest",
    ],
)
def test_raw_sql_rejects_mutations_privileged_sources_and_side_effects(sql):
    with pytest.raises(ValueError):
        validate_sql(sql, AgentSettings(_env_file=None))


def test_discovery_uses_live_registry_and_filters_not_a_table_allowlist():
    settings = AgentSettings(_env_file=None)
    added = Table(
        "workspace_test_discovered",
        Base.metadata,
        Column("id", Integer, primary_key=True),
        schema="core",
    )
    try:
        result = read_schema(settings, schema="core", table="workspace_test")
        assert result["tables"][0]["columns"][0]["primary_key"]
        assert "auth" not in {
            item["schema"] for item in read_schema(settings, schema="auth")["tables"]
        }
        assert "core.workspace_test_discovered" in validate_sql(
            "SELECT count(*) FROM core.workspace_test_discovered", settings
        )
    finally:
        Base.metadata.remove(added)


def test_select_join_and_cte_use_discovered_tables():
    sql = "WITH q AS (SELECT node_id FROM core.quest) SELECT q.node_id FROM q JOIN graph.node n ON n.id=q.node_id LIMIT 3"
    assert "WITH q AS" in validate_sql(sql, AgentSettings(_env_file=None))


def test_embedding_endpoint_and_dimensions_share_one_identity():
    settings = AgentSettings(
        _env_file=None,
        embedding_model="local-embed",
        embedding_dimensions=768,
        embedding_provider="chat",
        embedding_base_url="http://localhost:8080/v1",
    )
    route, payload = embedding_request(settings, ["source"])
    assert route == "/embeddings" and payload["dimensions"] == 768
    assert model_key(settings) != model_key(
        settings.model_copy(update={"embedding_dimensions": 1536})
    )
    assert model_key(settings) != model_key(
        settings.model_copy(update={"embedding_model": "another-model"})
    )


def test_local_embedding_endpoint_does_not_receive_the_main_provider_key():
    settings = AgentSettings(
        _env_file=None, api_key="main-secret", embedding_base_url="http://localhost:8080/v1"
    )
    assert not embedding_settings(settings).api_key.get_secret_value()
    settings.embedding_api_key = settings.api_key
    assert embedding_settings(settings).api_key.get_secret_value() == "main-secret"
    settings.read_database_url = settings.api_key
    config = settings.public_config()
    assert (
        "api_key" not in config
        and "embedding_api_key" not in config
        and "read_database_url" not in config
    )


@pytest.mark.asyncio
async def test_stale_memory_revision_does_not_overwrite_or_reindex():
    session = AsyncMock()
    session.scalar.return_value = None
    with pytest.raises(ValueError, match="revision conflict"):
        await save_memory(
            session,
            AgentSettings(_env_file=None),
            MemoryWrite(key="note", text="edit", expected_revision=2),
            run_id=1,
        )
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_audit_is_durable_after_caller_rollback_and_redacts_secrets():
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("""CREATE TABLE ops_agent_tool_audit (
                id INTEGER PRIMARY KEY, request_id TEXT, role TEXT, run_id INTEGER, step INTEGER,
                call_id TEXT, tool TEXT, status TEXT, arguments JSON, result JSON, duration_ms INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
            )
        # Translate only the schema; execute real ORM inserts/updates against SQLite.
        translated = engine.execution_options(schema_translate_map={"ops": None})
        async with translated.begin() as connection:
            await connection.execute(
                text("ALTER TABLE ops_agent_tool_audit RENAME TO agent_tool_audit")
            )
        async with AsyncSession(translated) as session:
            identity = await audit.start(
                session,
                request_id="request",
                role="public",
                call_id="call",
                tool="search",
                arguments={"api_key": "secret", "nested": {"authorization": "Bearer leaked"}},
            )
            await session.rollback()
            await audit.complete(session, identity, {"result": "ok"}, 12)
        async with AsyncSession(translated) as session:
            row = await session.scalar(select(AgentToolAudit))
            assert row.status == "completed" and row.duration_ms == 12
            assert row.arguments == {
                "api_key": "[redacted]",
                "nested": {"authorization": "[redacted]"},
            }
    finally:
        await engine.dispose()
