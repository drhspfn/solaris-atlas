"""Tile manifests and world-coordinate objects for an interactive map client."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.maps import MapMarker, MapTile, TileMap
from wuwa_story.db.models.storage import FileLocation, FileObject
from wuwa_story.db.session import get_session
from wuwa_story.storage.s3 import S3Storage

router = APIRouter(prefix="/maps", tags=["maps"])


def describe_map(row: TileMap) -> dict[str, Any]:
    size = row.tile_size
    return {"id": row.id, "game_version": row.game_version, "game_map_id": row.game_map_id,
            "asset_job_id": row.asset_job_id, "layer": row.layer_key, "tile_size": size,
            "grid_bounds": [row.min_x, row.min_y, row.max_x, row.max_y],
            "pixel_size": [(row.max_x - row.min_x + 1) * size, (row.max_y - row.min_y + 1) * size],
            "world_units_per_pixel": row.world_tile_size / size,
            "world_origin": [(row.min_x - 1) * row.world_tile_size,
                             -row.max_y * row.world_tile_size],
            "world_axes": [1, 1], "metadata": row.metadata_json}


async def get_map(session: AsyncSession, map_id: int) -> TileMap:
    row = await session.get(TileMap, map_id)
    if row is None:
        raise HTTPException(404, "Map not found")
    return row


@router.get("")
async def list_maps(game_version: str | None = None,
                    session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    query = select(TileMap)
    if game_version is not None:
        query = query.where(TileMap.game_version == game_version)
    rows = await session.scalars(query.order_by(TileMap.game_version, TileMap.game_map_id, TileMap.layer_key))
    return [describe_map(row) for row in rows]


@router.get("/{map_id}")
async def map_manifest(map_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    row = await get_map(session, map_id)
    tiles = list(await session.scalars(select(MapTile).where(MapTile.map_id == row.id).order_by(MapTile.y, MapTile.x)))
    ids = {tile.file_id for tile in tiles}
    ids.update(row.metadata_json.get("icon_file_ids", {}).values())
    if row.preview_file_id is not None:
        ids.add(row.preview_file_id)
    settings = get_settings()
    files = (await session.execute(select(FileObject, FileLocation).join(
        FileLocation, FileLocation.file_id == FileObject.id).where(
        FileObject.id.in_(ids), FileLocation.backend == "s3",
        FileLocation.bucket == settings.s3_bucket, FileLocation.available.is_(
            True),
        FileLocation.is_primary.is_(True)))).all()
    storage = S3Storage(settings)
    locations = {file.id: {"file_id": file.id, "sha256": file.sha256.hex() if file.sha256 else None,
                           "url": storage.public_url(location.object_key)} for file, location in files}
    return {**describe_map(row), "url_expires_in": 3600,
            "icons": {source: locations[file_id] for source, file_id in row.metadata_json.get("icon_file_ids", {}).items() if file_id in locations},
            "preview": locations.get(row.preview_file_id) if row.preview_file_id is not None else None,
            "tiles": [{"x": tile.x, "y": tile.y,
                       "pixel_x": (tile.x - row.min_x) * row.tile_size,
                       "pixel_y": (row.max_y - tile.y) * row.tile_size,
                       "image": locations.get(tile.file_id), "metadata": tile.metadata_json}
                      for tile in tiles]}


@router.get("/{map_id}/markers")
async def map_markers(map_id: int, category: str | None = None,
                      compact: bool = False,
                      min_x: float | None = Query(None, allow_inf_nan=False),
                      min_y: float | None = Query(None, allow_inf_nan=False),
                      max_x: float | None = Query(None, allow_inf_nan=False),
                      max_y: float | None = Query(None, allow_inf_nan=False),
                      include_hidden: bool = False, after_id: int = Query(0, ge=0),
                      limit: int = Query(1000, ge=1, le=5000),
                      session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    row = await get_map(session, map_id)
    if (min_x is not None and max_x is not None and min_x > max_x) or (
        min_y is not None and max_y is not None and min_y > max_y
    ):
        raise HTTPException(422, "Invalid world bounds")
    query = select(MapMarker).where(MapMarker.asset_job_id == row.asset_job_id,
                                    MapMarker.game_map_id == row.game_map_id, MapMarker.id > after_id)
    if category is not None:
        query = query.where(MapMarker.category == category)
    if not include_hidden:
        query = query.where(func.coalesce(
            MapMarker.metadata_json["hidden"].as_boolean(), False).is_(False))
    for column, minimum, maximum in ((MapMarker.world_x, min_x, max_x), (MapMarker.world_y, min_y, max_y)):
        if minimum is not None:
            query = query.where(column >= minimum)
        if maximum is not None:
            query = query.where(column <= maximum)
    markers = list(await session.scalars(query.order_by(MapMarker.id).limit(limit + 1)))
    more = len(markers) > limit
    markers = markers[:limit]
    scale = row.tile_size / row.world_tile_size
    # ConfigDB does not provide a reliable floor for every entity. Keep that
    # uncertainty explicit instead of assigning all markers to a floor layer.
    return {"floor_assignment": "unresolved", "items": [
        {"id": marker.id, "entity_id": marker.entity_id, "category": marker.category,
         "blueprint_type": marker.blueprint_type,
         "world": [marker.world_x, marker.world_y, marker.world_z],
         "pixel": [(marker.world_x - (row.min_x - 1) * row.world_tile_size) * scale,
                   (marker.world_y + row.max_y * row.world_tile_size) * scale],
         "metadata": ({key: value for key, value in marker.metadata_json.items()
                       if key in ("names", "description", "type_key", "area_ids", "floor", "hidden", "condition_id", "item_id", "icon_source", "resource_group", "drop_item_ids")}
                      if compact else marker.metadata_json)} for marker in markers],
        "next_after_id": markers[-1].id if more else None}
