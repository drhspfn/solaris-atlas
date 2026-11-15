import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wuwa_story.db.repositories.search import lexical_search as real_lexical_search

from wuwa_story_worker.video_analysis_agent import _execute_video_tool


@pytest.mark.asyncio
async def test_search_entities_tool_calls_lexical_search_with_keyword_args(monkeypatch):
    captured = {}

    async def fake_lexical_search(session, query, **kwargs):
        inspect.signature(real_lexical_search).bind(session, query, **kwargs)
        captured["query"] = query
        captured.update(kwargs)
        return [{"id": 42, "name": "Rover"}]

    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.lexical_search",
        fake_lexical_search,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[22, 1]))
    workspace = SimpleNamespace(duration=60.0, limits={})
    call = SimpleNamespace(
        name="search_entities",
        arguments={"query": "black-haired woman",
                   "category": "character", "limit": 10},
    )

    result, atlases = await _execute_video_tool(db, workspace, call)

    assert atlases == []
    assert result == {"results": [{"id": 42, "name": "Rover"}]}
    assert captured == {
        "query": "black-haired woman",
        "locale_id": 1,
        "categories": ["character"],
        "limit": 10,
    }
