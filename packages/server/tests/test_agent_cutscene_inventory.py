from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.agents import evidence


@pytest.mark.asyncio
async def test_inventory_includes_inferred_scenario_and_prefers_explicit(monkeypatch):
    monkeypatch.setattr(evidence, "quest_states", AsyncMock(return_value=[20]))
    session = SimpleNamespace(
        scalars=AsyncMock(side_effect=[[20, 21], [9]]),
        execute=AsyncMock(side_effect=[[(1, 100, "asset:a")],
                                       [(1, 100, "asset:a"), (2, 101, "asset:b")]]),
    )
    assert await evidence.quest_cutscene_assets(session, 5, 7) == [
        (1, 100, "asset:a", False), (2, 101, "asset:b", True),
    ]

    tools = evidence.EvidenceTools(session, run_id=1, quest=SimpleNamespace(node_id=5),
                                   release_id=7, locale=SimpleNamespace(), settings=SimpleNamespace())
    monkeypatch.setattr(evidence, "quest_cutscene_assets", AsyncMock(return_value=[(2, 101, "asset:b", True)]))
    monkeypatch.setattr(evidence, "visual_reference", AsyncMock(return_value=None))
    inventory = await tools.cutscene_inventory(tools.quest)
    assert inventory[0]["context_inferred"] is True
