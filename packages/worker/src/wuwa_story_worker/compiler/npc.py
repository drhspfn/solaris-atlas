"""Compile NPC characters from decoded native source tables."""
import json
from pathlib import Path
from typing import Any

from wuwa_story.ingestion.npc_sources import normalize_npcs

from .core import Writer, read_json


def _origin(npc: dict[str, Any], source: str, path: str) -> tuple[str, str] | None:
    if source == "template" and npc["template_row"] is not None:
        return ("BinData/template/templateconfig.json",
                f"$[{npc['template_row']}].ComponentsData{path[1:]}")
    if source.startswith("instance:"):
        item = npc["instances"][int(source.partition(":")[2])]["source"]
        return item["file"], f"{item['raw_path']}.ComponentsData{path[1:]}"
    return None


def compile_npcs(root: Path, writer: Writer, english: dict[str, dict[str, Any]]) -> dict[str, int]:
    base = root / "BinData"

    def rows(path: str) -> list[dict[str, Any]]:
        file = base / path
        return read_json(file) if file.is_file() else []

    shop_fixed = rows("shop/shopfixed.json")
    fixed_indexes = {row.get("Id"): index for index, row in enumerate(shop_fixed)}
    records = normalize_npcs(
        rows("blueprint/blueprintconfig.json"), rows("template/templateconfig.json"),
        rows("level_entity/levelentityconfig.json"), english,
        rows("shop/shopinfo.json"), shop_fixed,
        rows("AbpMontageData/abpmontagedata.json"), rows("MontageData/montagedata.json"),
        rows("speaker/speaker.json"), rows("model_config_preload/modelconfigpreload.json"),
    )
    for record in records:
        writer.emit("character", record)
        npc = record["npc"]

        for ref in npc["flow_references"]:
            triple = ref["flow"]
            if not isinstance(triple[0], str) or not triple[0] or any(type(v) is not int for v in triple[1:]):
                continue
            source = _origin(npc, ref["source"], ref["path"])
            if source:
                writer.edge(record["id"], f"flow_state:{triple[0]}_{triple[1]}_{triple[2]}",
                            "references_flow_state", "explicit NPC interaction flow triple", *source)
        if npc["speakers"]:
            name_source = (_origin(npc, "instance:0", "$.BaseInfoComponent.TidName")
                           if npc["instances"] and "TidName" in
                           npc["instances"][0]["components"].get("BaseInfoComponent", {})
                           else _origin(npc, "template", "$.BaseInfoComponent.TidName"))
            if name_source:
                writer.edge(record["id"], npc["speakers"][0], "has_speaker",
                            "exact NPC name key equals Speaker_<Id>_Name", *name_source)
        for shop in npc["shops"]:
            actions = [action for action in npc["shop_actions"] if action["shop_id"] == shop["id"]]
            for offer in shop["shopfixed"]:
                item_id = offer.get("ItemId")
                index = fixed_indexes.get(offer.get("Id"))
                if type(item_id) is not int or item_id <= 0 or index is None:
                    continue
                basis = json.dumps({"type": "authored_shop_offer", "shop_id": shop["id"],
                                    "quantity": offer.get("ItemNum"), "price": offer.get("Price"),
                                    "actions": [{"source": _origin(npc, a["source"], a["path"])} for a in actions]},
                                   ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                writer.edge(record["id"], f"item:{item_id}", "references_item", basis,
                            "BinData/shop/shopfixed.json", f"$[{index}].ItemId")
    return {"character": len(records)}
