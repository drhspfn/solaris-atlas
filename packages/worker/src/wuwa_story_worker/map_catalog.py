"""Source labels, gathering links and authored map marks for the world atlas."""

from collections import defaultdict
from pathlib import Path

from flatbuffers.table import Table

from wuwa_story_worker.map_sources import flag, integer, open_db, read_markers, table, text


def int_list(row: Table, field: int) -> list[int]:
    from flatbuffers.number_types import Int32Flags
    offset = row.Offset(4 + field * 2)
    return [row.Get(Int32Flags, row.Vector(offset) + i * 4)
            for i in range(row.VectorLen(offset))] if offset else []


def labels(config: Path) -> dict[str, dict[str, str]]:
    result = defaultdict(dict)
    for directory in sorted(config.iterdir()):
        if not directory.is_dir():
            continue
        for file in ("lang_multi_text.db", "lang_multi_text_1sthalf.db"):
            path = directory / file
            if not path.exists():
                continue
            with open_db(path) as db:
                for key, value in db.execute("SELECT Id, Content FROM MultiText WHERE "
                                             "Id LIKE 'Area_%' OR Id LIKE 'MapMark_%' OR Id LIKE 'ItemInfo_%' OR "
                                             "Id LIKE 'MonsterInfo_%' OR Id LIKE 'Country_%' OR Id LIKE 'MultiMap_%'"):
                    if value:
                        result[key].setdefault(directory.name, value)
    return dict(result)


def mark_category(label: str, icon: str) -> str:
    label = label.lower()
    if "resonance beacon" in label or "resonance nexus" in label:
        return "teleport"
    if "tacet field" in label:
        return "tacet_field"
    if "MonsterHead" in icon:
        return "boss"
    if "Shop" in icon or "MapNpc" in icon:
        return "shop"
    if "sonance" in label or "casket" in label or "windchimer" in label:
        return "collectible"
    if "treasure" in label:
        return "treasure_spot"
    if "Activity" in icon or "Act" in icon:
        return "activity"
    return "exploration"


def read_catalog(config: Path, map_ids: set[int]) -> tuple[list[dict], dict, dict]:
    translations = labels(config)
    areas = {}
    with open_db(config / "db_area.db") as db:
        for id, level, blob in db.execute("SELECT AreaId, Level, BinData FROM area"):
            row = table(blob)
            areas[id] = {"id": id, "level": level, "parent_id": integer(row, 8),
                         "names": translations.get(text(row, 7), {})}
    items = {}
    with open_db(config / "db_item.db") as db:
        for id, blob in db.execute("SELECT Id, BinData FROM iteminfo"):
            row = table(blob)
            items[id] = translations.get(text(row, 2), {})
    resources = {}
    with open_db(config / "db_enrichment.db") as db:
        for (blob,) in db.execute("SELECT BinData FROM enrichmentareaconfig"):
            row = table(blob)
            for entity in int_list(row, 4):
                resources[integer(row, 3), entity] = integer(row, 2)
    monsters = {}
    with open_db(config / "db_monster_Info.db") as db:
        for (blob,) in db.execute("SELECT BinData FROM monsterinfo"):
            row = table(blob)
            monsters[text(row, 7)] = translations.get(text(row, 1), {})
    markers = read_markers(config, map_ids)
    by_entity = {(m["game_map_id"], m["entity_id"]): m for m in markers}
    entity_positions = {}
    # Also retain named monsters and source positions referenced by authored marks.
    with open_db(config / "db_level_entity.db") as db:
        for map_id, entity_id, blueprint, blob in db.execute(
            "SELECT MapId, EntityId, BlueprintType, BinData FROM levelentityconfig"
        ):
            if map_id not in map_ids:
                continue
            row = table(blob)
            offset = row.Offset(22)
            if not offset or not row.VectorLen(offset):
                continue
            pos = Table(blob, row.Indirect(row.Vector(offset)))
            xyz = [integer(pos, i) / 100 for i in range(3)]
            entity_positions[map_id, entity_id] = (xyz, integer(row, 7))
            if (map_id, entity_id) not in by_entity and blueprint in monsters:
                marker = {"game_map_id": map_id, "entity_id": entity_id, "category": "monster",
                          "blueprint_type": blueprint, "world_x": xyz[0], "world_y": xyz[1],
                          "world_z": xyz[2], "metadata_json": {"area_id": integer(row, 7),
                                                               "hidden": flag(row, 6), "names": monsters[blueprint], "floor": None}}
                markers.append(marker)
                by_entity[map_id, entity_id] = marker

    def ancestors(area_id):
        result = []
        while area_id in areas and area_id not in result:
            result.append(area_id)
            area_id = areas[area_id]["parent_id"]
        return result
    for marker in markers:
        meta = marker["metadata_json"]
        item = resources.get((marker["game_map_id"], marker["entity_id"]))
        if item:
            marker["category"] = "resource"
            meta.update(item_id=item, names=items.get(item, {}))
        meta.setdefault("names", {})
        meta.update(type_key=f"item:{item}" if item else marker["blueprint_type"],
                    area_ids=ancestors(meta.get("area_id", 0)), source_kind="entity")
    unresolved = 0
    with open_db(config / "db_map_mark.db") as db:
        for id, map_id, entity_id, blob in db.execute("SELECT MarkId, MapId, EntityConfigId, BinData FROM mapmark"):
            if map_id not in map_ids:
                continue
            row = table(blob)
            position = entity_positions.get((map_id, entity_id))
            if not position:
                offset = row.Offset(58)
                if offset:
                    vector = Table(blob, row.Indirect(row.Pos + offset))
                    xyz = [integer(vector, i) for i in range(3)]
                    if any(xyz):
                        position = (xyz, 0)
            if not position:
                unresolved += 1
                continue
            xyz, area_id = position
            names = translations.get(text(row, 18), {})
            icon = text(row, 21) or text(row, 20)
            category = mark_category(names.get("en", ""), icon)
            floor = integer(row, 24) or None
            existing = by_entity.get((map_id, entity_id))
            if existing and floor:
                existing["metadata_json"]["floor"] = floor
            markers.append({"game_map_id": map_id, "entity_id": -id, "category": category,
                            "blueprint_type": "MapMark", "world_x": xyz[0], "world_y": xyz[1], "world_z": xyz[2],
                            "metadata_json": {"names": names, "description": translations.get(text(row, 19), {}),
                                              "icon_source": icon, "type_key": text(row, 18) or icon.rsplit("/", 1)[-1].split(".")[0] or category,
                                              "area_id": area_id, "area_ids": ancestors(area_id), "floor": floor,
                                              "hidden": False, "source_kind": "mapmark", "mark_id": id,
                                              "linked_entity_id": entity_id, "gravity": integer(row, 28),
                                              "condition_id": integer(row, 12)}})
    bounds = defaultdict(list)
    for marker in markers:
        for area_id in marker["metadata_json"].get("area_ids", []):
            if areas[area_id]["level"] == 2 and areas[area_id]["names"]:
                bounds[marker["game_map_id"], area_id].append(
                    [marker["world_x"], marker["world_y"]])
    map_details = defaultdict(lambda: {"locations": []})
    for (map_id, area_id), points in bounds.items():
        map_details[map_id]["locations"].append({**areas[area_id], "bounds": [
            min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]})
    with open_db(config / "db_map.db") as db:
        for (blob,) in db.execute("SELECT BinData FROM multimap"):
            row = table(blob)
            map_details[integer(row, 2)].setdefault("floors", {})[str(integer(row, 0))] = {
                "floor": integer(row, 4), "group_id": integer(row, 1),
                "names": translations.get(text(row, 10), {})}
    return markers, dict(map_details), {"unpositioned_map_marks": unresolved}
