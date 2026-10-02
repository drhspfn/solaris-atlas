import hashlib
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.api.app import app
from wuwa_story.api.routes import maps
from wuwa_story.db.models.maps import MapMarker, TileMap
from wuwa_story.db.session import get_session


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Test PostgreSQL not configured")
async def test_map_api_coordinates_bounds_and_pagination():
    engine = create_async_engine(os.environ["WUWA_TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        job_id = hashlib.sha256(uuid4().bytes).hexdigest()
        async with factory() as session, session.begin():
            row = TileMap(asset_job_id=job_id, game_version="3.7.0", game_map_id=8,
                          layer_key="gravity:1", tile_size=1024, world_tile_size=85000,
                          min_x=-1, max_x=-1, min_y=-2, max_y=-2)
            session.add(row)
            for entity_id, hidden in ((1, False), (2, False), (3, True)):
                session.add(MapMarker(asset_job_id=job_id, game_map_id=8, entity_id=entity_id,
                                      category="chest", blueprint_type="Treasure001",
                                      world_x=-127500, world_y=212500, world_z=100,
                                      metadata_json={"hidden": hidden}))
            await session.flush()
            map_id = row.id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get(f"/maps/{map_id}/markers", params={"limit": 1})
            assert result.status_code == 200
            body = result.json()
            assert len(body["items"]) == 1
            assert body["items"][0]["pixel"] == [512, 512]
            assert body["items"][0]["world"] == [-127500, 212500, 100]
            assert body["floor_assignment"] == "unresolved"
            result = await client.get(f"/maps/{map_id}/markers", params={"after_id": body["next_after_id"]})
            assert len(result.json()["items"]) == 1
            assert result.json()["next_after_id"] is None
            result = await client.get(f"/maps/{map_id}/markers", params={"include_hidden": True})
            assert len(result.json()["items"]) == 3
            result = await client.get(f"/maps/{map_id}/markers", params={"min_x": 0})
            assert result.json()["items"] == []
            result = await client.get(f"/maps/{map_id}/markers", params={"min_x": 1, "max_x": -1})
            assert result.status_code == 422
            assert (await client.get("/maps/999999999/markers")).status_code == 404
            async with factory() as session:
                value = maps.describe_map(await maps.get_map(session, map_id))
                assert value["world_origin"] == [-170000, 170000]
                assert value["world_axes"] == [1, 1]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
