from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from wuwa_story.ingestion import weapon_backfill

@pytest.mark.asyncio
async def test_weapon_backfill_dry_run_skips_existing_release_items(monkeypatch):
    db=AsyncMock()
    db.__aenter__.return_value=db
    db.get.return_value=SimpleNamespace(id=4)
    db.scalars.side_effect=[
        [SimpleNamespace(data={'ItemId':21010001}),SimpleNamespace(data={'ItemId':21020001})],
        ['item:21010001']]
    monkeypatch.setattr(weapon_backfill,'SessionFactory',lambda:db)
    assert await weapon_backfill.backfill(4,dry_run=True)=={'release_id':4,'missing_weapons':1,'imported':0}
    db.commit.assert_not_awaited()
