from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes import nodes, story
from wuwa_story.story import service


@pytest.mark.asyncio
async def test_node_context_prefetches_target_and_neighbors_before_rendering(monkeypatch):
    target = (Row(node_id=1), Row(node_id=8), Row(node_id=9, state_key="flow", source_record_id=None), None, None, None)
    neighbors = [target, (Row(node_id=2), target[1], target[2], None, None, None)]
    session = Row(
        get=AsyncMock(side_effect=[Row(key="dialogue_line"), Row(action_node_id=8, source_index=0)]),
        execute=AsyncMock(side_effect=[Row(first=lambda: target), Row(all=lambda: neighbors), Row(first=lambda: None)]),
        scalars=AsyncMock(side_effect=[[], []]),
    )
    monkeypatch.setattr(nodes, "get_node", AsyncMock(return_value=Row(id=1, canonical_key="line:1", type_id=1)))
    monkeypatch.setattr(story, "_release_id", AsyncMock(return_value=None))
    prefetched = AsyncMock()
    monkeypatch.setattr(service, "prefetch_dialogue", prefetched)

    async def payload(_session, row, **_kwargs):
        prefetched.assert_awaited_once_with(session, [target, *neighbors], "en", None)
        return {"id": row[0].node_id, "action": {"params": {}}, "provenance": {"raw_record": {}}}

    monkeypatch.setattr(story, "_dialogue_payload", payload)
    result = await nodes.node_narrative_context("line:1", session=session)
    assert result["dialogue"]["id"] == 1
    assert [line["id"] for line in result["context"]] == [1, 2]
    assert "params" not in result["dialogue"]["action"]
    assert "raw_record" not in result["context"][1]["provenance"]
