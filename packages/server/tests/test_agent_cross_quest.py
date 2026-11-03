from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

from wuwa_story.agents.contracts import AnalysisResult
from wuwa_story.agents.settings import AgentSettings


@pytest.mark.asyncio
async def test_cross_quest_resolution_is_staged_without_modifying_the_old_document():
    from wuwa_story.agents.evidence import EvidenceTools
    from wuwa_story.agents.providers import ToolCall

    session = AsyncMock()
    session.get.return_value = Row(
        kind="question",
        revision=1,
        node_id=1,
        citations=[{"node_id": 7, "snapshot_id": 1, "quote": "Who left?"}],
    )
    tools = EvidenceTools(
        session,
        run_id=5,
        quest=Row(node_id=2),
        release_id=1,
        locale=Row(id=1, code="en"),
        settings=AgentSettings(_env_file=None),
        evidence={7: "Who left?", 8: "The traveller left."},
        source_release_ids=[1],
        source_evidence={"1:7": "Who left?", "1:8": "The traveller left."},
    )
    tools.read_node = AsyncMock(
        side_effect=lambda node_id, locale: {
            "text": "Who left?" if node_id == 7 else "The traveller left."
        }
    )
    output = await tools.call(
        ToolCall(
            "resolution",
            "resolve_question",
            {
                "work_id": 4,
                "expected_revision": 1,
                "status": "resolved",
                "explanation": "The other quest identifies the traveller.",
                "citations": [
                    {"node_id": 7, "snapshot_id": 1, "quote": "Who left?"},
                    {"node_id": 8, "snapshot_id": 1,
                        "quote": "The traveller left."},
                ],
            },
        )
    )
    assert output["status"] == "staged_for_publication"
    assert output["staged_resolution"]["work_id"] == 4
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_editorial_sections_cannot_skip_authored_passages():
    from wuwa_story.agents.evidence import EvidenceTools

    tools = EvidenceTools(
        AsyncMock(),
        run_id=5,
        quest=Row(node_id=1),
        release_id=1,
        locale=Row(id=1, code="en"),
        settings=AgentSettings(_env_file=None),
    )
    tools.target_positions = AsyncMock(return_value={7: 0, 8: 1, 9: 2})
    result = AnalysisResult(
        title="Quest",
        blocks=[
            {"title": "Summary", "text": "Story", "citations": [
                {"node_id": 7, "quote": "Story"}]}
        ],
        sections=[
            {
                "key": "ending",
                "title": "Ending",
                "description": "The end",
                "start_node_id": 8,
                "end_node_id": 9,
                "citations": [{"node_id": 8, "quote": "Start"}, {"node_id": 9, "quote": "End"}],
            }
        ],
    )
    with pytest.raises(ValueError, match="ordered"):
        await tools.validate_result(result, require_full_quest=True)
