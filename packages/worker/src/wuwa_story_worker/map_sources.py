"""Read the client's FlatBuffer map configuration (verified against 3.7 readers)."""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from pathlib import Path

from flatbuffers.number_types import BoolFlags, Int32Flags
from flatbuffers.table import Table

WORLD_TILE_SIZE = 85_000.0


def table(blob: bytes) -> Table:
    return Table(blob, struct.unpack_from("<I", blob)[0])


def integer(row: Table, field: int, default: int = 0) -> int:
    offset = row.Offset(4 + field * 2)
    return row.Get(Int32Flags, row.Pos + offset) if offset else default


def text(row: Table, field: int) -> str:
    offset = row.Offset(4 + field * 2)
    return row.String(row.Pos + offset).decode("utf-8") if offset else ""


def strings(row: Table, field: int) -> list[str]:
    offset = row.Offset(4 + field * 2)
    return [row.String(row.Vector(offset) + index * 4).decode("utf-8")
            for index in range(row.VectorLen(offset))] if offset else []


def flag(row: Table, field: int) -> bool:
    offset = row.Offset(4 + field * 2)
    return bool(row.Get(BoolFlags, row.Pos + offset)) if offset else False


def open_db(path: Path) -> sqlite3.Connection:
    # Missing inputs must fail, rather than silently creating an empty SQLite DB.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def resource_path(path: str) -> str:
    if not path.startswith("/Game/") or ".." in path.split("/"):
        raise ValueError(f"Invalid Unreal resource path: {path}")
    return "Client/Content/" + path[6:].split(".")[0] + ".uasset"


def read_tiles(config: Path) -> list[dict]:
    with open_db(config / "db_ui_resource.db") as db:
        resources = {key: path for key, path in db.execute(
            "SELECT Id, Path FROM uiresource")}
    result = []
    with open_db(config / "db_mapfog.db") as db:
        for identifier, block, map_id, gravity, blob in db.execute(
            "SELECT Id, Block, MapId, GravityFlip, BinData FROM fogtextureconfig ORDER BY Id"
        ):
            row = table(blob)
            key = text(row, 4)
            if not key:
                continue
            match = re.fullmatch(r"(-?\d+)_(-?\d+)", block)
            if not match:
                raise ValueError(f"Unsupported tile block: {block}")
            result.append({"map_id": map_id, "layer": f"gravity:{gravity}",
                           "x": int(match[1]), "y": int(match[2]),
                           "source_path": resource_path(resources[key]),
                           "source_id": identifier, "gravity": gravity})
    with open_db(config / "db_map.db") as db:
        for (blob,) in db.execute("SELECT BinData FROM multimap"):
            row = table(blob)
            for key in strings(row, 5):
                path = resource_path(resources[key])
                # The game's MapTileMgr reads the same signed indices for floor tiles.
                match = re.match(r"T_[^_]+_(-?\d+)_(-?\d+)_", Path(path).stem)
                if not match:
                    raise ValueError(f"Unsupported floor tile: {path}")
                result.append({"map_id": integer(row, 2),
                               "layer": f"floor:{integer(row, 0)}",
                               "x": int(match[1]), "y": int(match[2]), "source_path": path,
                               "source_id": str(integer(row, 0)), "floor": integer(row, 4),
                               "group_id": integer(row, 1), "gravity": integer(row, 3, 1)})
    identities = set()
    for tile in result:
        identity = tile["map_id"], tile["layer"], tile["x"], tile["y"]
        if identity in identities:
            raise ValueError(f"Duplicate tile placement: {identity}")
        identities.add(identity)
    if not result:
        raise ValueError("No configured map tiles found")
    return result


def read_markers(config: Path, map_ids: set[int]) -> list[dict]:
    result = []
    with open_db(config / "db_level_entity.db") as db:
        for source_id, map_id, entity_id, blueprint, blob in db.execute(
            "SELECT Id, MapId, EntityId, BlueprintType, BinData FROM levelentityconfig ORDER BY Id"
        ):
            if map_id not in map_ids:
                continue
            category = ("chest" if "Treasure" in blueprint else
                        "collectible" if "Collect" in blueprint else None)
            if category is None:
                continue
            row = table(blob)
            offset = row.Offset(22)
            if not offset or row.VectorLen(offset) < 1:
                raise ValueError(f"Missing entity position: {source_id}")
            position = Table(blob, row.Indirect(row.Vector(offset)))
            components = json.loads(text(row, 10) or "{}")
            # CreatureModel divides ConfigDB IntVector by 100 before using world coordinates.
            x, y, z = (integer(position, field) / 100 for field in range(3))
            result.append({"game_map_id": map_id, "entity_id": entity_id, "category": category,
                           "blueprint_type": blueprint, "world_x": x, "world_y": y, "world_z": z,
                           "metadata_json": {"source_id": source_id, "area_id": integer(row, 7),
                                             "hidden": flag(row, 6), "in_sleep": flag(row, 5),
                                             "components": components,
                                             "category_basis": "blueprint_name", "floor": None}})
    return result


def world_to_pixel(x: float, y: float, min_x: int, max_y: int, tile_size: int) -> tuple[float, float]:
    """UI Y points up; raster Y points down. Tile rows therefore run max_y to min_y."""
    return ((x / WORLD_TILE_SIZE - (min_x - 1)) * tile_size,
            (y / WORLD_TILE_SIZE + max_y) * tile_size)
