from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.agents.contracts import AnalysisResult
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.agents.work import dispatch_work, publish_questions
from wuwa_story.agents.workspace import QuestionResolution, resolve_question


@pytest.mark.asyncio
async def test_autonomous_dispatch_is_opt_in_without_database_or_paid_calls():
    session = AsyncMock()
    assert await dispatch_work(session, AgentSettings(_env_file=None)) is None
    session.scalar.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_respects_global_pending_limit_before_discovery():
    session = AsyncMock()
    session.scalar.side_effect = [True, None, 1]
    session.get.return_value = None
    settings = AgentSettings(
        _env_file=None, api_key="test", daily_budget_usd=1, autonomous_enabled=True
    )
    assert await dispatch_work(session, settings) is None
    assert session.scalar.await_count == 3
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_question_republication_preserves_resolution_and_has_stable_identity():
    session = AsyncMock()
    document = Row(id=9, node_id=7, locale_id=1, metadata_json={"release_id": 2})
    result = AnalysisResult(
        title="Quest",
        blocks=[
            {
                "title": "Summary",
                "text": "Story",
                "kind": "summary",
                "citations": [{"node_id": 7, "quote": "Story"}],
            }
        ],
        unresolved_questions=["Where did the traveller go?"],
    )
    await publish_questions(session, document, result)
    statement = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(statement)
    # Reanalysis refreshes the originating document, not an already sourced answer.
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "resolution =" not in update_clause and "status =" not in update_clause
    assert "question:7:1:2:" in statement.params["key"]
    assert statement.params["status"] == "open"


@pytest.mark.asyncio
async def test_question_resolution_is_compare_and_swap_and_does_not_commit():
    session = AsyncMock()
    session.scalar.return_value = None
    request = QuestionResolution(
        work_id=4,
        expected_revision=2,
        status="resolved",
        explanation="Answered in the next quest",
        citations=[{"node_id": 8, "quote": "The traveller returned", "snapshot_id": 1}],
    )
    with pytest.raises(ValueError, match="changed"):
        await resolve_question(session, request, run_id=10)
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "agent_work_item.revision =" in sql and "agent_work_item.kind =" in sql
    session.commit.assert_not_awaited()
