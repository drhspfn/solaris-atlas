"""Public NPC locations require an actual marker on a published map."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.npc_profile import npc_record
from wuwa_story.api.routes.story.shared import _version_key
from wuwa_story.db.models.maps import MapMarker, TileMap
from wuwa_story.db.session import get_session

router = APIRouter(tags=["story browsing"])


def location_names(marker, atlas):
    catalog = (atlas.metadata_json or {}).get("catalog") or {}
    areas = []
    for area in catalog.get("locations") or []:
        bounds = area.get("bounds") or []
        if len(bounds) == 4 and all(isinstance(value, (int, float)) for value in bounds):
            x1, y1, x2, y2 = bounds
            if x1 <= marker.world_x <= x2 and y1 <= marker.world_y <= y2 and area.get("names"):
                areas.append(((x2 - x1) * (y2 - y1), area["names"]))
    return min(areas, key=lambda entry: entry[0])[1] if areas else catalog.get("names") or {}


@router.get("/npcs/{canonical_key:path}/locations")
async def npc_locations(canonical_key: str, game_version: str | None = None,
                        limit: int = Query(8, ge=1, le=24), offset: int = Query(0, ge=0),
                        session: AsyncSession = Depends(get_session)):
    _, _, npc, _ = await npc_record(session, canonical_key, game_version)
    pairs = sorted({(item.get("map_id"), item.get("entity_id"))
                    for item in npc.get("instances") or []
                    if type(item.get("map_id")) is int and type(item.get("entity_id")) is int})
    empty = {"results": [], "total": 0, "limit": limit, "offset": offset}
    if not pairs:
        return empty
    atlases = list(await session.scalars(select(TileMap).where(TileMap.layer_key.like("gravity:%"))))
    worlds = {}
    for atlas in sorted(atlases, key=lambda row: (_version_key(row.game_version), row.id), reverse=True):
        previous = worlds.get(atlas.game_map_id)
        if previous is None or (previous.asset_job_id == atlas.asset_job_id and atlas.layer_key < previous.layer_key):
            worlds[atlas.game_map_id] = atlas
    if not worlds:
        return empty
    candidates = select(MapMarker.id, TileMap.id.label("atlas_id")).join(TileMap, and_(
        TileMap.asset_job_id == MapMarker.asset_job_id, TileMap.game_map_id == MapMarker.game_map_id,
    )).where(TileMap.id.in_([world.id for world in worlds.values()]),
             tuple_(MapMarker.game_map_id, MapMarker.entity_id).in_(pairs)).subquery()
    total = await session.scalar(select(func.count()).select_from(candidates)) or 0
    page = select(candidates.c.id, candidates.c.atlas_id).order_by(candidates.c.atlas_id, candidates.c.id).offset(offset).limit(limit).subquery()
    rows = await session.execute(select(MapMarker, TileMap).join(page, page.c.id == MapMarker.id)
                                .join(TileMap, TileMap.id == page.c.atlas_id).order_by(page.c.atlas_id, page.c.id))
    return {"results": [{"id": marker.id, "names": location_names(marker, atlas),
                         "map_names": (atlas.metadata_json or {}).get("catalog", {}).get("names", {}),
                         "href": f"/map?map={atlas.id}&marker={marker.id}",
                         "source": {"map_id": marker.game_map_id, "entity_id": marker.entity_id,
                                    "game_version": atlas.game_version}}
                        for marker, atlas in rows], "total": total, "limit": limit, "offset": offset}
