from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes.story import npc_locations as module


def test_location_label_uses_smallest_containing_authored_area():
    marker = Row(world_x=15, world_y=20)
    atlas = Row(metadata_json={"catalog": {"names": {"en": "World"}, "locations": [
        {"names": {"en": "Region"}, "bounds": [0, 0, 100, 100]},
        {"names": {"en": "Town"}, "bounds": [10, 10, 30, 30]},
        {"names": {"en": "Other"}, "bounds": [50, 50, 70, 70]},
    ]}})
    assert module.location_names(marker, atlas) == {"en": "Town"}
    marker.world_x = 150
    assert module.location_names(marker, atlas) == {"en": "World"}


@pytest.mark.asyncio
async def test_no_placement_returns_no_fake_map_link(monkeypatch):
    monkeypatch.setattr(module, "npc_record", AsyncMock(return_value=(None, None, {}, 21)))
    session = Row(scalars=AsyncMock())
    assert await module.npc_locations("character:npc:1", limit=8, offset=0, session=session) == {
        "results": [], "total": 0, "limit": 8, "offset": 0}
    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_map_location_requires_matching_published_job_entity_and_pages_before_metadata(monkeypatch):
    monkeypatch.setattr(module, "npc_record", AsyncMock(return_value=(None, None,
        {"instances": [{"map_id": 8, "entity_id": 100}]}, 21)))
    old = Row(id=1, game_map_id=8, asset_job_id="old", game_version="3.9.0", layer_key="gravity:0")
    atlas = Row(id=2, game_map_id=8, asset_job_id="new", game_version="3.10.0", layer_key="gravity:0",
                metadata_json={"catalog": {"names": {"en": "Town"}}})
    marker = Row(id=10, entity_id=100, game_map_id=8, world_x=1, world_y=1)
    session = Row(scalars=AsyncMock(return_value=[old, atlas]), scalar=AsyncMock(return_value=30),
                  execute=AsyncMock(return_value=[(marker, atlas)]))
    result = await module.npc_locations("character:npc:1", limit=8, offset=8, session=session)
    assert result["total"] == 30 and result["results"][0]["href"] == "/map?map=2&marker=10"
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "asset_job_id" in str(compiled) and "entity_id" in str(compiled)
    assert "LIMIT" in str(compiled) and "OFFSET" in str(compiled)
    assert [2] in compiled.params.values()
    assert 8 in compiled.params.values()
