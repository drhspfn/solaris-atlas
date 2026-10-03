"""Exact published placement/reward references for item acquisition navigation."""

import re

from sqlalchemy import or_, select

from wuwa_story.db.models.maps import MapMarker, TileMap

ITEM_QUEST_REWARD_LIMIT = 50


def group_item_markers(rows, item_id):
    groups = {}
    for marker, atlas in rows:
        meta = marker.metadata_json
        direct = meta.get("item_id") == item_id
        if not direct and item_id not in meta.get("drop_item_ids", []):
            continue
        # Match the map menu's authored type grouping; a numeric representative
        # marker identifies the group without putting filter lists in the URL.
        type_name = (
            f"item:{meta['item_id']}"
            if meta.get("item_id")
            else "dream-patrol"
            if marker.category == "combat_activity"
            else meta.get("names", {}).get("en")
            or meta.get("icon_source")
            or meta.get("type_key")
            or marker.blueprint_type
        )
        key = atlas.id, marker.category, type_name
        group = groups.setdefault(
            key,
            {
                "map_id": atlas.id,
                "game_version": atlas.game_version,
                "map_names": atlas.metadata_json.get("catalog", {}).get("names", {}),
                "names": meta.get("names", {}),
                "category": marker.category,
                "kind": "gathering" if direct else "loot_preview",
                "count": 0,
                "hidden_count": 0,
                "url": f"/map?map={atlas.id}&item={item_id}&source={marker.id}",
            },
        )
        group["count"] += 1
        group["hidden_count"] += bool(meta.get("hidden"))
    return list(groups.values())


async def item_map_sources(session, item_id):
    # A marker set belongs to a specific imported asset job. Choose one base
    # atlas per world from the newest published version, never mix job markers.
    atlases = await session.scalars(
        select(TileMap)
        .where(TileMap.layer_key.like("gravity:%"))
        .order_by(TileMap.game_version.desc(), TileMap.id.desc())
    )
    worlds = {}
    for atlas in sorted(
        atlases,
        key=lambda row: (tuple(int(part) for part in re.findall(r"\d+", row.game_version)), row.id),
        reverse=True,
    ):
        if atlas.game_map_id not in worlds or (
            atlas.asset_job_id == worlds[atlas.game_map_id].asset_job_id
            and atlas.layer_key < worlds[atlas.game_map_id].layer_key
        ):
            worlds[atlas.game_map_id] = atlas
    if not worlds:
        return []
    conditions = [
        (MapMarker.asset_job_id == atlas.asset_job_id)
        & (MapMarker.game_map_id == atlas.game_map_id)
        for atlas in worlds.values()
    ]
    markers = await session.scalars(
        select(MapMarker)
        .where(
            or_(*conditions),
            or_(
                MapMarker.metadata_json.contains({"item_id": item_id}),
                MapMarker.metadata_json.contains({"drop_item_ids": [item_id]}),
            ),
        )
        .order_by(MapMarker.id)
    )
    return group_item_markers([(marker, worlds[marker.game_map_id]) for marker in markers], item_id)
