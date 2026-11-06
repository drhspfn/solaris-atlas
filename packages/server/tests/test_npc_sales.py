import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story import npc_sales


@pytest.mark.asyncio
async def test_reverse_sellers_uses_shop_evidence_only(monkeypatch):
    monkeypatch.setattr(npc_sales, "_release_id", AsyncMock(return_value=5))
    node = SimpleNamespace(id=1, canonical_key="character:npc:1", slug=None)
    proof = json.dumps({"type": "authored_shop_offer", "shop_id": 140,
                        "quantity": 3, "price": {"1": 20}})
    session = SimpleNamespace(execute=AsyncMock(return_value=[
        (node, "I.R.I.S.", 7, proof), (node, "I.R.I.S.", 7, "unrelated item reference")]))
    monkeypatch.setattr(npc_sales, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_sales, "_localized", AsyncMock(return_value={"content": "I.R.I.S."}))
    result = await npc_sales.item_npc_sellers(100, limit=20, offset=0, session=session)
    assert result["total"] == 1
    assert result["results"][0]["canonical_key"] == "character:npc:1"
    assert result["results"][0]["offers"] == [
        {"shop_id": 140, "quantity": 3, "price": {"1": 20}}]


@pytest.mark.asyncio
async def test_reverse_sellers_use_scoped_indexed_name_for_ambiguous_npc(monkeypatch):
    monkeypatch.setattr(npc_sales, "_release_id", AsyncMock(return_value=5))
    node = SimpleNamespace(id=1, canonical_key="character:npc:17290", slug=None)
    proof = json.dumps({"type": "authored_shop_offer", "shop_id": 140})
    class Rows:
        def all(self):
            return [(1, "Zahira")]
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[(node, None, None, proof)], Rows()]))
    monkeypatch.setattr(npc_sales, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(npc_sales, "_localized", AsyncMock(return_value=None))
    result = await npc_sales.item_npc_sellers(100, locale="en", limit=20, offset=0, session=session)
    assert result["results"][0]["name"] == "Zahira"
