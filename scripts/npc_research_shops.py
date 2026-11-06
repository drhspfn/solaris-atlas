"""Extract local NPC shop evidence; SQLite inputs are opened read-only.

Example (run from the repository root):
  python scripts/npc_research_shops.py --config-root scratch/npc-research-20261106/shop-export --js-root scratch/npc-research-20261106/source-export --out scratch/npc-research-20261106/shop-evidence.json

Only authored OpenSystemBoard/Shop actions establish entity-to-shop ownership.
Flow references and conditions are retained, not evaluated as live game state.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import struct
from contextlib import closing
from pathlib import Path

LEVEL_ENTITIES = Path("E:/Caches/solaris-model-lab/quest-story-proof/levelentityconfig.json")
INFO_FIELDS = ["Id", "ShopType", "ShopName", "OpenId", "ShowCurrency", "DialogueText", "UiCamera"]
FIXED_FIELDS = ["Id", "ShopId", "SortIndex", "ItemId", "ItemNum", "CondId",
                "VisableCondition", "DisableCondition", "LimitNum", "OriginalPrice",
                "Price", "Show", "Label", "SwitchText", "PurchaseText"]


def source(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "sha256": digest}


def discover(root, name):
    matches = sorted(root.rglob(name))
    if len(matches) != 1:
        raise ValueError(f"Expected one {name} under {root}; found {len(matches)}")
    return matches[0]


def walk(value, path=""):
    """Yield JSON pointers and objects, including actions inside conditional branches."""
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from walk(child, f"{path}/{escaped}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}/{index}")


def entity_shops(rows):
    for index, row in enumerate(rows):
        hits = [{"pointer": f"/{index}{pointer}", "action": obj}
                for pointer, obj in walk(row)
                if obj.get("Name") == "OpenSystemBoard"
                and isinstance(obj.get("Params"), dict)
                and obj["Params"].get("SystemType") == "Shop"]
        if not hits:
            continue
        components = row.get("ComponentsData") or {}
        interact = components.get("InteractComponent") or {}
        preflow = interact.get("PreFlow") or {}
        flows = []
        for pointer, obj in walk({"InteractComponent": interact,
                                  "NpcPerformComponent": components.get("NpcPerformComponent")}):
            if "StateId" not in obj or not ("FlowId" in obj or pointer.endswith("Flow")):
                continue
            resolved = {key: obj.get(key, preflow.get(key))
                        for key in ("FlowListName", "FlowId", "StateId")}
            flows.append({"pointer": f"/{index}/ComponentsData{pointer}", "raw": obj,
                          "resolved": resolved,
                          "resolution": "explicit" if all(key in obj for key in resolved)
                          else "inherits missing flow identity from PreFlow; candidate"})
        yield {"source_pointer": f"/{index}",
               "entity": {key: row.get(key) for key in
                          ("Id", "MapId", "EntityId", "BlueprintType", "Name", "AreaId", "Transform")},
               "shop_actions": hits, "PreFlow": interact.get("PreFlow"),
               "Options": interact.get("Options", []),
               "NpcPerformComponent": components.get("NpcPerformComponent"), "flows": flows}


# These are the same FlatBuffer offsets used by the exported ShopInfo/ShopFixed
# JS readers. Keep this bounded reader standalone, without server/DB dependencies.
class ShopRow:
    def __init__(self, blob, position=None):
        self.blob = blob
        self.position = self.unpack("I", 0) if position is None else position

    def unpack(self, kind, position):
        return struct.unpack_from("<" + kind, self.blob, position)[0]

    def offset(self, field):
        vtable = self.position - self.unpack("i", self.position)
        slot = 4 + field * 2
        offset = self.unpack("H", vtable + slot) if slot < self.unpack("H", vtable) else 0
        return self.position + offset if offset else None

    def number(self, field, default=0, kind="i"):
        offset = self.offset(field)
        return default if offset is None else self.unpack(kind, offset)

    def string(self, field):
        offset = self.offset(field)
        if offset is None:
            return None
        offset += self.unpack("I", offset)
        length = self.unpack("I", offset)
        value = self.blob[offset + 4:offset + 4 + length]
        if len(value) != length:
            raise ValueError("Truncated FlatBuffer string")
        return value.decode("utf-8")

    def vector(self, field, mapping=False):
        offset = self.offset(field)
        result = {} if mapping else []
        if offset is not None:
            offset += self.unpack("I", offset)
            for index in range(self.unpack("I", offset)):
                item = offset + 4 + index * 4
                if mapping:
                    row = ShopRow(self.blob, item + self.unpack("I", item))
                    result[str(row.number(0))] = row.number(1)
                else:
                    result.append(self.unpack("i", item))
        return result


def decode(blob, fixed=False):
    row = ShopRow(blob)
    if not fixed:
        return dict(zip(INFO_FIELDS, [row.number(0), row.number(1), row.string(2),
                    row.number(3), row.vector(4), row.string(5), row.string(6)]))
    values = [row.number(i, {4: 1, 8: -1}.get(i, 0)) for i in range(9)]
    values += [row.vector(9, True), row.vector(10, True), bool(row.number(11, 1, "b"))]
    values += [row.string(i) for i in range(12, 15)]
    return dict(zip(FIXED_FIELDS, values))


def js_evidence(root, relative, needles):
    path = root / relative
    code = path.read_text(encoding="utf-8-sig")
    snippets = []
    for needle in needles:
        start = code.find(needle)
        if start < 0:
            raise ValueError(f"Unrecognized client JS: {path}: missing {needle}")
        snippets.append({"needle": needle, "line": code[:start].count("\n") + 1,
                         "character_offset": start, "excerpt": code[max(0, start - 60):start + len(needle) + 200]})
    return {**source(path), "snippets": snippets}


def collect(level_entities, config_root, js_root):
    db_path = discover(config_root, "db_shop.db")
    js = discover(js_root, "ShopInfo.js").parents[3]
    proofs = {
        "dispatch": js_evidence(js, "Game/LevelGamePlay/LevelEvents/LevelEventOpenSystem.js",
                                ['this.KDe.set("Shop",new OpenSystemShopView_1.OpenSystemShopView']),
        "board_to_shop": js_evidence(js, "Game/LevelGamePlay/LevelEvents/OpenSystem/OpenSystemShopView.js",
                                     ["ShopController.OpenShop(e.BoardId"]),
        "shop_config": js_evidence(js, "Game/Module/Shop/ShopConfig.js",
                                   ["GetShopInfoConfig", "GetFixedShopList"]),
        "info_query": js_evidence(js, "Core/Define/ConfigQuery/ShopInfoById.js",
                                  ['DB="db_shop.db"', 'where Id = ?']),
        "items_query": js_evidence(js, "Core/Define/ConfigQuery/ShopFixedByShopId.js", ['where ShopId = ?']),
        "refresh_value": js_evidence(js, "Game/Module/Shop/ShopDefine.js", ["this.uMo=t.Lxs"]),
        "refresh_update": js_evidence(js, "Game/Module/Shop/ShopModel.js", ["t.UpdateRefreshTime(e.Lxs)"]),
        "refresh_countdown": js_evidence(js, "Game/Module/Shop/ShopView.js",
                                        ["this.u3e-TimeUtil_1.TimeUtil.GetServerTime()", "void 0===this.u3e||0===this.u3e"]),
        "cross_day": js_evidence(js, "Game/Module/Shop/ShopController.js", ["EEventName.CrossDay", "_Mo=()=>"]),
    }
    schemas = {}
    for name, fields in [("ShopInfo", INFO_FIELDS), ("ShopFixed", FIXED_FIELDS)]:
        path = js / f"Core/Define/Config/{name}.js"
        code = path.read_text(encoding="utf-8-sig")
        getters = re.findall(r"get (\w+)\(\)", code)
        if getters != fields:
            raise ValueError(f"Unsupported {name} schema: {getters}")
        # Validate scalar offsets, defaults and vector shape against the JS below
        # with the independent JS round-trip check when updating these readers.
        schemas[name] = {**source(path), "fields": getters,
                         "refresh_fields": [key for key in getters if re.search("refresh|reset", key, re.IGNORECASE)]}
    entities = list(entity_shops(json.loads(level_entities.read_bytes())))
    shops = {}
    unresolved = []
    for entity in entities:
        for hit in entity["shop_actions"]:
            board = hit["action"]["Params"].get("BoardId")
            if type(board) is not int or board <= 0:
                unresolved.append({**hit, "reason": "missing/invalid BoardId; no shop mapping"})
                continue
            shops.setdefault(board, {"BoardId": board, "ShopInfo": None, "ShopFixed": []})
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for board, shop in sorted(shops.items()):
            for identity, blob in db.execute("SELECT Id, BinData FROM shopinfo WHERE Id = ?", (board,)):
                data = decode(blob)
                if data["Id"] != identity:
                    raise ValueError(f"ShopInfo identity mismatch: {identity}")
                shop["ShopInfo"] = {"data": data, "table": "shopinfo", "key": identity,
                                    "blob_sha256": hashlib.sha256(blob).hexdigest()}
            for identity, blob in db.execute("SELECT Id, BinData FROM shopfixed WHERE ShopId = ? ORDER BY Id", (board,)):
                data = decode(blob, fixed=True)
                if (data["Id"], data["ShopId"]) != (identity, board):
                    raise ValueError(f"ShopFixed identity mismatch: {identity}")
                shop["ShopFixed"].append({"data": data, "table": "shopfixed", "key": identity,
                                          "blob_sha256": hashlib.sha256(blob).hexdigest()})
            shop["mapping_status"] = "config_confirmed" if shop["ShopInfo"] else "missing_shopinfo"
    return {"format": "solaris-npc-shop-evidence-v1",
            "sources": {"level_entities": source(level_entities), "shop_db": source(db_path)},
            "js_evidence": proofs, "schemas": schemas,
            "refresh": {"status": "server_supplied; authored period not established",
                        "period": None, "next_reset": None, "runtime_field": "Lxs",
                        "evidence": ["refresh_value", "refresh_update", "refresh_countdown", "cross_day"],
                        "notes": ["ShopInfo and ShopFixed contain no refresh/reset fields.",
                                  "CrossDay requests fresh shop data; it does not prove daily stock reset.",
                                  "Zero/undefined UpdateTime hides the countdown; no live response was sampled.",
                                  "Configured stock/prices/conditions are not a live inventory snapshot."]},
            "counts": {"shop_entities": len(entities), "shop_actions": sum(len(e["shop_actions"]) for e in entities),
                       "shop_ids": len(shops), "unresolved_actions": len(unresolved),
                       "missing_shopinfo": sum(s["ShopInfo"] is None for s in shops.values())},
            "unresolved_actions": unresolved,
            "entities": entities, "shops": [shops[key] for key in sorted(shops)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level-entities", type=Path, default=LEVEL_ENTITIES)
    parser.add_argument("--config-root", type=Path, required=True)
    parser.add_argument("--js-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.level_entities, args.config_root, args.js_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
