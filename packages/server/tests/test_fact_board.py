from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from wuwa_story.agents import capabilities, fact_board
from wuwa_story.agents.cutscene_vision import VideoEntityLink


def test_video_entity_link_accepts_character_and_quest_relations():
    link = VideoEntityLink(
        target_node_id=42,
        target_name="Lily",
        kind="character",
        relation="character_arc_in",
        confidence="observed",
        evidence="Lily forgives and accepts herself at the climax.",
    )
    assert link.relation == "character_arc_in"


@pytest.mark.asyncio
async def test_post_fact_and_execute_fact_board_tool(monkeypatch):
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[101, 7, SimpleNamespace(id=303)])
    session.scalars = AsyncMock(return_value=[10, 20])
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    result = await capabilities.execute_fact_board_tool(
        session,
        "post_fact",
        {
            "fact_type": "character_arc",
            "title": "Lily forgives herself",
            "description": "In cutscene M0399, Lily accepts her past.",
            "importance": 5,
            "confidence": "observed",
            "subject_node_id": 10,
            "object_node_id": 20,
            "relation_type": "character_arc_in",
        },
        run_id=55,
        source_kind="video_analysis",
    )
    assert result is not None
    assert result["id"] == 101
    assert result["status"] == "active"


@pytest.mark.asyncio
async def test_link_fact_to_question():
    session = MagicMock()
    fact = SimpleNamespace(id=5, question_id=None)
    question = SimpleNamespace(id=99, kind="question")
    session.get = AsyncMock(side_effect=[fact, question])
    session.flush = AsyncMock()

    res = await fact_board.link_fact_to_question(session, fact_id=5, question_id=99)
    assert res == {"fact_id": 5, "question_id": 99, "linked": True}
    assert fact.question_id == 99


def test_memory_write_import_from_workspace():
    from wuwa_story.agents.workspace import MemoryWrite

    item = MemoryWrite.model_validate(
        {"key": "long:test", "scope": "long", "text": "note", "expected_revision": 0}
    )
    assert item.key == "long:test"
