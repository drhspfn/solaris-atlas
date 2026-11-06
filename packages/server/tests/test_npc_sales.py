import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story import npc_sales


@pytest.mark.asyncio
async def test_reverse_sellers_uses_shop_evidence_only(monkeypatch):
    monkeypatch.setattr(npc_sales, "_release_id", AsyncMock(return_value=5))
    node = SimpleNamespace(canonical_key="character:npc:1", slug=None)
    proof = json.dumps({"type": "authored_shop_offer", "shop_id": 140,
                        "quantity": 3, "price": {"1": 20}})
    session = SimpleNamespace(execute=AsyncMock(return_value=[
        (node, "I.R.I.S.", proof), (node, "I.R.I.S.", "unrelated item reference")]))
    result = await npc_sales.item_npc_sellers(100, limit=20, offset=0, session=session)
    assert result["total"] == 1
    assert result["results"][0]["canonical_key"] == "character:npc:1"
    assert result["results"][0]["offers"] == [
        {"shop_id": 140, "quantity": 3, "price": {"1": 20}}]
