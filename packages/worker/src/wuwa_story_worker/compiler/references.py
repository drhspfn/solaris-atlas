"""Normalize only explicit narrative pointers in ancillary BinData tables."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import Writer, read_json

FLOW_TABLES = {
    "BubbleData/bubbledata.json": "bubble_action",
    "InteractData/interactdata.json": "interaction",
    "LevelPlayData/levelplaydata.json": "level_play",
    "LevelPlayNodeData/levelplaynodedata.json": "level_play_node",
    "GuessJokerCard/guessjokerplotconfig.json": "minigame_plot",
    "PhantomBattle/phantombattledialog.json": "battle_dialog_pointer",
    "PhantomBattle/phantombattlewinseq.json": "battle_win_sequence",
    "instance_dungeon/instancedungeon.json": "instance_dungeon_quest_reference",
}
QUEST_TABLES = {
    "QuestRefMapBlock/questrefmapblockconfig.json": "quest_map_block_config",
    "QuestTreeCustomJumpConfig/questtreecustomjumpconfig.json": "quest_custom_jump_config",
    "RefResourceQuestList/refresourcequestlist.json": "quest_resource_config",
}


def _decode(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            return value
    return value


def _walk(value: Any, path: str = "$"):
    original = value
    value = _decode(value)
    if value is not original and isinstance(original, str):
        yield from _walk(value, f"{path}<json>")
        return
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def compile_reference_tables(root: Path, writer: Writer,
                             localize: Any = None,
                             inventory: dict[str, Any] | None = None) -> dict[str, int]:
    """Emit raw-traceable records and links where source fields are explicit.

    Flow triples are accepted only when all three fields coexist in the same
    raw object. Quest references use fields explicitly named QuestId.
    """
    counts: dict[str, int] = {}
    quest_ids = {entity_id.removeprefix("quest:") for entity_id in writer.ids
                 if entity_id.startswith("quest:")}
    flow_ids = {entity_id.removeprefix("flow:") for entity_id in writer.ids
                if entity_id.startswith("flow:")}
    state_keys = {entity_id.removeprefix("flow_state:") for entity_id in writer.ids
                  if entity_id.startswith("flow_state:")}

    map_source = "BinData/DownLoad/mapblockinfo.json"
    map_path = root / map_source
    map_block_ids: set[int] = set()
    if map_path.is_file():
        for row_index, raw in enumerate(read_json(map_path)):
            block_id = raw.get("BlockId") if isinstance(raw, dict) else None
            if not isinstance(block_id, int):
                writer.diagnostic("unsupported_map_block_record", "warning", map_source,
                                  f"$[{row_index}]", raw)
                continue
            identity = f"map_block:{block_id}"
            writer.emit("map_block", {"id": identity, "block_id": block_id,
                                       "map_id": raw.get("MapId"),
                                       "source": {"file": map_source, "row": row_index,
                                                  "raw_path": f"$[{row_index}]"},
                                       "raw": raw})
            map_block_ids.add(block_id)
    else:
        writer.diagnostic("missing_reference_table", "warning", map_source, "$", None)
    counts["map_block"] = len(map_block_ids)

    for relative, kind in FLOW_TABLES.items():
        source = f"BinData/{relative}"
        path = root / source
        if not path.is_file():
            writer.diagnostic("missing_reference_table", "warning", source, "$", None)
            continue
        rows = read_json(path)
        count = 0
        for row_index, raw in enumerate(rows):
            references = []
            for raw_path, value in _walk(raw):
                if not isinstance(value, dict):
                    continue
                list_name_field = "FlowListName" if isinstance(value.get("FlowListName"), str) else (
                    "PlotName" if relative == "PhantomBattle/phantombattledialog.json" and
                    isinstance(value.get("PlotName"), str) else None)
                list_name = value.get(list_name_field) if list_name_field else None
                flow_number = value.get("FlowId")
                state_number = value.get("StateId")
                if not isinstance(list_name, str) or flow_number is None or state_number is None:
                    continue
                flow_id = f"{list_name}_{flow_number}"
                state_key = f"{flow_id}_{state_number}"
                references.append((raw_path, flow_id, state_key, list_name_field))
            quest_refs = []
            if relative in ("InteractData/interactdata.json", "LevelPlayData/levelplaydata.json",
                            "LevelPlayNodeData/levelplaynodedata.json"):
                quest_refs = [(f"{p}.QuestId", value["QuestId"]) for p, value in _walk(raw)
                              if isinstance(value, dict) and isinstance(value.get("QuestId"), int)
                              and (relative == "InteractData/interactdata.json" or
                                   any(token in p for token in ("Condition", "PreChildQuest")))]
            elif relative == "instance_dungeon/instancedungeon.json":
                quest_refs = [(f"{p}.RelatedQuestId", value["RelatedQuestId"])
                              for p, value in _walk(raw) if isinstance(value, dict)
                              and isinstance(value.get("RelatedQuestId"), int)
                              and value["RelatedQuestId"] != 0]
            if not references and not quest_refs:
                continue
            identity = f"source_reference:{relative}:{row_index}"
            writer.emit("source_reference", {
                "id": identity, "reference_kind": kind,
                "source": {"file": source, "row": row_index, "raw_path": f"$[{row_index}]"},
                "raw": raw,
                **({"localization": localize(raw.get("TidContent"))}
                   if localize is not None and isinstance(raw, dict) and
                   isinstance(raw.get("TidContent"), str) else {}),
            })
            count += 1
            for raw_path, flow_id, state_key, list_name_field in references:
                if flow_id not in flow_ids:
                    writer.diagnostic("unresolved_external_flow", "warning", source,
                                      f"$[{row_index}]" + raw_path[1:],
                                      {"flow_id": flow_id, "state_key": state_key})
                elif state_key not in state_keys:
                    writer.diagnostic("unresolved_external_flow_state", "warning", source,
                                      f"$[{row_index}]" + raw_path[1:],
                                      {"flow_id": flow_id, "state_key": state_key})
                else:
                    writer.edge(identity, f"flow_state:{state_key}", "references_flow_state",
                                f"{list_name_field} + FlowId + StateId in one source object",
                                source, f"$[{row_index}]" + raw_path[1:],
                                {"flow_id": flow_id, "state_key": state_key,
                                                  "reference_kind": kind})
            for raw_path, quest_id in quest_refs:
                target = f"quest:{quest_id}"
                relation = "runtime_condition" if any(token in raw_path for token in
                                                       ("Condition", "PreChildQuest")) else "explicit_reference"
                if str(quest_id) in quest_ids:
                    writer.edge(identity, target, "condition_references_quest" if relation == "runtime_condition"
                                else "references_quest", "QuestId", source,
                                f"$[{row_index}]" + raw_path[1:],
                                {"relation": relation})
                else:
                    writer.diagnostic("unresolved_external_quest", "warning", source,
                                      f"$[{row_index}]" + raw_path[1:], quest_id)
        counts[kind] = count

    for relative, kind in QUEST_TABLES.items():
        source = f"BinData/{relative}"
        path = root / source
        if not path.is_file():
            writer.diagnostic("missing_reference_table", "warning", source, "$", None)
            continue
        rows = read_json(path)
        count = 0
        for row_index, raw in enumerate(rows):
            quest_id = raw.get("QuestId") if isinstance(raw, dict) else None
            if not isinstance(quest_id, int):
                writer.diagnostic("unsupported_quest_reference_record", "warning", source,
                                  f"$[{row_index}]", raw)
                continue
            identity = f"source_reference:{relative}:{row_index}"
            writer.emit("source_reference", {
                "id": identity, "reference_kind": kind,
                "source": {"file": source, "row": row_index, "raw_path": f"$[{row_index}]"},
                "raw": raw,
            })
            count += 1
            if str(quest_id) in quest_ids:
                writer.edge(f"quest:{quest_id}", identity, "has_reference_config", "QuestId",
                            source, f"$[{row_index}].QuestId", {"reference_kind": kind})
            else:
                writer.diagnostic("unresolved_external_quest", "warning", source,
                                  f"$[{row_index}].QuestId", quest_id)
            if relative == "QuestRefMapBlock/questrefmapblockconfig.json":
                block_values = raw.get("MapBlockId") or []
                if isinstance(block_values, int):
                    block_values = [block_values]
                if not isinstance(block_values, list):
                    writer.diagnostic("unsupported_map_block_reference", "warning", source,
                                      f"$[{row_index}].MapBlockId", block_values)
                    continue
                for block_index, block_id in enumerate(block_values):
                    if block_id in map_block_ids:
                        writer.edge(identity, f"map_block:{block_id}", "references_map_block",
                                    "MapBlockId[] = mapblockinfo.BlockId", source,
                                    f"$[{row_index}].MapBlockId[{block_index}]")
                    else:
                        writer.diagnostic("unresolved_map_block", "warning", source,
                                          f"$[{row_index}].MapBlockId[{block_index}]", block_id)
        counts[kind] = count

    # For Tier 1 tables without a dedicated normalizer, retain the whole source
    # row and materialize only explicit QuestId/full flow-triple references.
    # This records reference evidence without guessing the table's full purpose.
    dedicated = set(FLOW_TABLES) | set(QUEST_TABLES)
    for table in (inventory or {}).get("tables", []):
        rel = table.get("file", "").removeprefix("BinData/")
        if (not rel or rel in dedicated or table.get("narrative_tier") != "tier1" or
                table.get("status") != "unknown"):
            continue
        path = root / "BinData" / rel
        if not path.is_file():
            continue
        try:
            rows = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            writer.diagnostic("unreadable_reference_candidate", "error", f"BinData/{rel}", "$", str(exc))
            continue
        if not isinstance(rows, list):
            continue
        count = 0
        for row_index, raw in enumerate(rows):
            flow_refs = []
            quest_refs = []
            for raw_path, value in _walk(raw):
                if not isinstance(value, dict):
                    continue
                list_name, flow_number, state_number = (value.get("FlowListName"),
                                                        value.get("FlowId"), value.get("StateId"))
                if isinstance(list_name, str) and flow_number is not None and state_number is not None:
                    flow_refs.append((raw_path, f"{list_name}_{flow_number}",
                                      f"{list_name}_{flow_number}_{state_number}"))
                if isinstance(value.get("QuestId"), int):
                    quest_refs.append((f"{raw_path}.QuestId", value["QuestId"]))
            if not flow_refs and not quest_refs:
                continue
            identity = f"source_reference:{rel}:{row_index}"
            writer.emit("source_reference", {
                "id": identity, "reference_kind": "generic_explicit_reference",
                "source": {"file": f"BinData/{rel}", "row": row_index,
                           "raw_path": f"$[{row_index}]"}, "raw": raw,
            })
            count += 1
            for raw_path, flow_id, state_key in flow_refs:
                full_path = f"$[{row_index}]" + raw_path[1:]
                if flow_id in flow_ids and state_key in state_keys:
                    writer.edge(identity, f"flow_state:{state_key}", "references_flow_state",
                                "FlowListName + FlowId + StateId in one source object",
                                f"BinData/{rel}", full_path,
                                {"flow_id": flow_id, "state_key": state_key,
                                 "reference_kind": "generic_explicit_reference"})
                else:
                    writer.diagnostic("unresolved_external_flow_state", "warning", f"BinData/{rel}",
                                      full_path, {"flow_id": flow_id, "state_key": state_key})
            for raw_path, quest_id in quest_refs:
                full_path = f"$[{row_index}]" + raw_path[1:]
                if str(quest_id) not in quest_ids:
                    writer.diagnostic("unresolved_external_quest", "warning", f"BinData/{rel}",
                                      full_path, quest_id)
                    continue
                conditional = any(token in raw_path for token in ("Condition", "PreChildQuest"))
                writer.edge(identity, f"quest:{quest_id}",
                            "condition_references_quest" if conditional else "references_quest",
                            "QuestId", f"BinData/{rel}", full_path,
                            {"relation": "runtime_condition" if conditional else "explicit_reference",
                             "reference_kind": "generic_explicit_reference"})
        if count:
            counts[f"generic:{rel}"] = count

    # Promote exact entity-id fields from any table, even when the table itself
    # has no dedicated narrative normalizer. The table row is retained as raw
    # evidence and only exact ids in a known namespace become graph edges.
    entity_fields = {
        "ItemId": ("item", "references_item"),
        "ItemID": ("item", "references_item"),
        "AreaId": ("area", "references_area"),
        "AreaID": ("area", "references_area"),
        "RoleId": ("character", "references_character"),
        "RoleID": ("character", "references_character"),
        "CharacterId": ("character", "references_character"),
        "CharacterID": ("character", "references_character"),
        "NpcId": ("npc", "references_npc"),
        "NpcID": ("npc", "references_npc"),
        "SpeakerId": ("speaker", "has_speaker"),
        "SpeakerID": ("speaker", "has_speaker"),
    }
    entity_targets: dict[str, set[str]] = {}
    for field, (kind, _) in entity_fields.items():
        prefix = {"character": "character", "speaker": "speaker", "npc": "npc",
                  "item": "item", "area": "area"}[kind]
        entity_targets[field] = {identifier for identifier in writer.ids
                                 if identifier.startswith(f"{prefix}:")}
    for table in (inventory or {}).get("tables", []):
        rel = table.get("file", "").removeprefix("BinData/")
        fields = set(table.get("all_field_names", []))
        if rel in {"area/area.json", "item/iteminfo.json", "role/roleinfo.json",
                   "speaker/speaker.json", "npc_headinfo/npcheadinfo.json"}:
            continue
        if not rel or not fields.intersection(entity_fields):
            continue
        path = root / "BinData" / rel
        if not path.is_file():
            continue
        try:
            rows = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            writer.diagnostic("unreadable_entity_reference_table", "error",
                              f"BinData/{rel}", "$", str(exc))
            continue
        if not isinstance(rows, list):
            continue
        emitted = 0
        for row_index, raw in enumerate(rows):
            found = []
            for raw_path, value in _walk(raw):
                if not isinstance(value, dict):
                    continue
                for field, (kind, relation) in entity_fields.items():
                    target_value = value.get(field)
                    if isinstance(target_value, (str, int)) and not isinstance(target_value, bool):
                        found.append((raw_path, field, kind, relation, str(target_value)))
            if not found:
                continue
            identity = f"source_reference:{rel}:{row_index}"
            if identity not in writer.ids:
                writer.emit("source_reference", {
                    "id": identity, "reference_kind": "explicit_entity_reference",
                    "source": {"file": f"BinData/{rel}", "row": row_index,
                               "raw_path": f"$[{row_index}]"}, "raw": raw,
                })
            emitted += 1
            for raw_path, field, kind, relation, target_value in found:
                target = f"{kind}:{target_value}"
                full_path = f"$[{row_index}]" + raw_path[1:] + f".{field}"
                if target in entity_targets[field]:
                    writer.edge(identity, target, relation,
                                f"exact {field} value resolves in {kind} namespace",
                                f"BinData/{rel}", full_path,
                                {"resolution_basis": "exact_namespace_id", "raw_value": target_value})
                else:
                    writer.diagnostic("unresolved_explicit_entity_reference", "warning",
                                      f"BinData/{rel}", full_path,
                                      {"field": field, "value": target_value, "target_kind": kind})
        if emitted:
            counts[f"explicit_entity_refs:{rel}"] = emitted
    return counts
