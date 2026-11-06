"""Normalize decoded NPC source rows without accessing storage or the database."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def _components(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value or "{}")
    return value if isinstance(value, dict) else {}


def _walk(value: Any, path: str = "$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _npc(base: dict[str, Any], components: dict[str, Any], entity_type: Any) -> bool:
    category = (base.get("Category") or {}).get("MainType")
    if category is not None:
        return category == "Npc"
    return (entity_type == "Npc" and
            (components.get("ModelComponent") or {}).get("ModelType", {}).get("Type") == "Npc")


def normalize_npcs(blueprints: list[dict[str, Any]], templates: list[dict[str, Any]],
                   entities: list[dict[str, Any]], english: dict[str, dict[str, Any]],
                   shop_info: list[dict[str, Any]] = (), shop_fixed: list[dict[str, Any]] = (),
                   abp_montages: list[dict[str, Any]] = (), montages: list[dict[str, Any]] = (),
                   speakers: list[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """Return one character per BlueprintConfig.Id, preserving authored overrides."""
    template_by_type = {row.get("BlueprintType"): (index, row) for index, row in enumerate(templates)}
    instances: dict[Any, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(entities):
        instances[row.get("BlueprintType")].append((index, row))
    speaker_keys = {f"Speaker_{row['Id']}_Name": f"speaker:{row['Id']}"
                    for row in speakers if row.get("Id") is not None}
    info_by_id: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    fixed_by_shop: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in shop_info:
        info_by_id[row.get("Id")].append(row)
    for row in shop_fixed:
        fixed_by_shop[row.get("ShopId")].append(row)
    montage_by_id = {row.get("Id"): row for row in montages}
    abp_by_id = {row.get("Id"): row for row in abp_montages}

    def localization(key: Any) -> dict[str, Any] | None:
        if not isinstance(key, str) or not key:
            return None
        found = english.get(key) or {}
        return {"key": key, "en": found.get("content"), "source": found.get("source"),
                "resolution": found.get("resolution", "resolved_nonempty") if found else "missing_key"}

    result = []
    for index, bp in enumerate(blueprints):
        identifier, blueprint = bp.get("Id"), bp.get("BlueprintType")
        if type(identifier) is not int or identifier <= 0 or not isinstance(blueprint, str) or not blueprint:
            continue
        template_index, template = template_by_type.get(blueprint, (None, {}))
        components = _components(template.get("ComponentsData"))
        base = components.get("BaseInfoComponent") or {}
        selected = []
        for row_index, row in instances.get(blueprint, []):
            overrides = _components(row.get("ComponentsData"))
            effective_base = {**base, **(overrides.get("BaseInfoComponent") or {})}
            if not _npc(effective_base, components, bp.get("EntityType")):
                continue
            selected.append({"map_id": row.get("MapId"), "entity_id": row.get("EntityId"),
                             "components": overrides, "name": localization(effective_base.get("TidName")),
                             "client_source": row.get("_client_source"),
                             "source": {"file": "BinData/level_entity/levelentityconfig.json",
                                        "row": row_index, "raw_path": f"$[{row_index}]"}})
        if not _npc(base, components, bp.get("EntityType")) and not selected:
            continue
        variants: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for instance in selected:
            value = instance["name"]
            variants[value["key"] if value else None].append(instance["source"])
        if selected:
            sole_key = next(iter(variants)) if len(variants) == 1 else None
            name = localization(sole_key)
        else:
            name = localization(base.get("TidName"))
        shop_ids: set[int] = set()
        shop_actions = []
        flows = []
        montage_refs = []
        for source, value in [("template", components), *[(f"instance:{i}", row["components"]) for i, row in enumerate(selected)]]:
            for path, obj in _walk(value):
                params = obj.get("Params")
                if obj.get("Name") == "OpenSystemBoard" and isinstance(params, dict) and params.get("SystemType") == "Shop":
                    board = params.get("BoardId")
                    if type(board) is int and board > 0:
                        shop_ids.add(board)
                        shop_actions.append({"shop_id": board, "source": source, "path": path})
                if all(key in obj for key in ("FlowListName", "FlowId", "StateId")):
                    flows.append({"flow": [obj["FlowListName"], obj["FlowId"], obj["StateId"]],
                                  "source": source, "path": path, "basis": "explicit_flow_triple"})
                if type(obj.get("MontageId")) is int:
                    montage_refs.append({"id": obj["MontageId"], "is_abp": obj.get("IsAbp"),
                                         "source": source, "path": path})
        proven_speakers = [speaker_keys[name["key"]]] if name and name["key"] in speaker_keys else []
        npc = {"blueprint_id": identifier, "blueprint_type": blueprint, "template_id": template.get("Id"),
               "source": bp.get("_client_source"),
               "template_source": template.get("_client_source"),
               "template_row": template_index,
               "components": components, "instances": selected, "model": components.get("ModelComponent"),
               "name_variants": [{"name": localization(key), "sources": sources}
                                 for key, sources in sorted(variants.items(), key=lambda item: item[0] or "")],
               "shop_ids": sorted(shop_ids), "shop_actions": shop_actions,
               "speakers": proven_speakers, "flow_references": flows,
               "shops": [{"id": sid, "shopinfo": info_by_id[sid],
                          "shopfixed": fixed_by_shop[sid]}
                         for sid in sorted(shop_ids)],
               "montage_references": montage_refs,
               "montages": [{"reference": ref, "row":
                              (abp_by_id if ref["is_abp"] else montage_by_id).get(ref["id"])} for ref in montage_refs
                             if isinstance(ref["is_abp"], bool)]}
        result.append({"id": f"character:npc:{identifier}", "game_id": identifier, "name": name,
                       "shared_artwork_paths": [],
                       "source": {"file": "BinData/blueprint/blueprintconfig.json", "row": index,
                                  "raw_path": f"$[{index}]"}, "raw": bp, "npc": npc})
    return result
