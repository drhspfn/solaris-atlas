"""Compile authored quest structure from datamined configuration tables.

This module deliberately does not infer a traversal order among siblings.  Quest
node parentage, condition branches, and flow references have different meanings
and remain distinct edges with their original field paths.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

Emit = Callable[[str, dict[str, Any]], None]
Edge = Callable[[str, str, str, str, dict[str, Any], str, dict[str, Any] | None], None]
Diagnostic = Callable[[str, str, dict[str, Any], str, Any], None]


def _rows(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError(f"Expected JSON array in {path}")
    return rows


def _data(value: Any, source: dict[str, Any], diagnostic: Diagnostic) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        diagnostic("invalid_embedded_json", "error", source, "Data", str(exc))
        return value


def _source(path: str | Path, index: int) -> dict[str, Any]:
    parts = Path(path).parts
    if "BinData" in parts:
        file = Path(*parts[parts.index("BinData"):]).as_posix()
    else:
        file = Path(path).as_posix()
    return {"file": file, "row": index, "raw_path": f"$[{index}]", "array_index": index}


def _visit(value: Any, path: str = "Data"):
    """Yield every nested value and its exact JSON path, including array positions."""
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _visit(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _visit(child, f"{path}[{index}]")


def compile_quests(
    paths: Mapping[str, str | Path],
    emit: Emit,
    edge: Edge,
    diagnostic: Diagnostic,
    localize: Callable[[str], Any] | None = None,
) -> dict[str, int]:
    """Emit quest, quest-node, plot-step, scene, and referenced flow-state data.

    Required ``paths`` keys: ``quest_data``, ``quest_nodes``, ``plot_handbook``,
    ``flow``, ``flow_state``.  Dimbreath's JSON-encoded ``Data`` values and
    Arikatsu's object values are both accepted.  Callback records contain
    ``id``, ``source``, and the unmodified ``raw`` source record; parsed ``data``
    is included when a field was JSON-encoded.  ``localize`` is optional and is
    called only for explicit Tid* keys; its result is never used as identity.
    """
    missing = set(("quest_data", "quest_nodes", "plot_handbook", "flow", "flow_state")) - set(paths)
    if missing:
        raise ValueError(f"Missing source paths: {', '.join(sorted(missing))}")

    edge_callback = edge
    diagnostic_callback = diagnostic

    def edge(from_id: str, to_id: str, type: str, basis: str,
             source: dict[str, Any], raw_path: str, extra: dict[str, Any] | None = None) -> None:
        full_path = raw_path if raw_path.startswith("$") else f"$[{source['row']}].{raw_path}"
        edge_callback(from_id, to_id, type, basis, source["file"], full_path, extra)

    def diagnostic(code: str, severity: str, source: dict[str, Any],
                   raw_path: str, detail: Any) -> None:
        full_path = raw_path if raw_path.startswith("$") else f"$[{source['row']}].{raw_path}"
        diagnostic_callback(code, severity, source["file"], full_path, detail)

    counts: Counter[str] = Counter()
    quest_rows = _rows(paths["quest_data"])
    node_rows = _rows(paths["quest_nodes"])
    plot_path = Path(paths["plot_handbook"])
    if plot_path.is_file():
        plot_rows = _rows(plot_path)
    else:
        # Older game snapshots predate PlotHandBook; preserve that schema gap
        # explicitly while continuing to compile QuestData/QuestNodeData/Flow.
        diagnostic_callback("missing_optional_source_table", "warning", str(plot_path), "$",
                            {"table": "PlotHandBook", "reason": "not present in this source snapshot"})
        plot_rows = []
    optional_tables = {}
    for table in ("quest_types", "quest_chapters", "quest_tree_nodes", "quest_tree_chapters"):
        path = paths.get(table)
        if path is None or not Path(path).is_file():
            if path is not None:
                diagnostic_callback("missing_optional_source_table", "warning", str(path), "$",
                                    {"table": table, "reason": "not present in this source snapshot"})
            optional_tables[table] = []
        else:
            optional_tables[table] = _rows(path)
    flow_rows = _rows(paths["flow"])
    state_rows = _rows(paths["flow_state"])
    flow_ids = {row.get("Id") for row in flow_rows if isinstance(row, dict)}
    state_by_key = {row.get("StateKey"): (index, row) for index, row in enumerate(state_rows) if isinstance(row, dict)}
    quest_ids = {row.get("QuestId") for row in quest_rows if isinstance(row, dict)}
    node_ids = {row.get("Key") for row in node_rows if isinstance(row, dict)}
    quest_type_ids = {row.get("Id") for row in optional_tables["quest_types"] if isinstance(row, dict)}
    chapter_ids = {row.get("Id") for row in optional_tables["quest_chapters"] if isinstance(row, dict)}
    tree_node_ids = {row.get("Id") for row in optional_tables["quest_tree_nodes"] if isinstance(row, dict)}
    tree_chapter_ids = {row.get("Id") for row in optional_tables["quest_tree_chapters"] if isinstance(row, dict)}
    emitted_states: set[str] = set()

    def link_flow(owner: str, flow: dict[str, Any], source: dict[str, Any], raw_path: str, basis: str) -> None:
        name, flow_id, state_id = (flow.get(k) for k in ("FlowListName", "FlowId", "StateId"))
        if not name:
            return
        identity = f"{name}_{flow_id}"
        state_key = f"{identity}_{state_id}"
        if identity not in flow_ids:
            diagnostic("unresolved_flow", "warning", source, raw_path, {"flow_id": identity})
            counts["unresolved_flow"] += 1
        state = state_by_key.get(state_key)
        if state is None:
            diagnostic("unresolved_flow_state", "warning", source, raw_path, {"state_key": state_key})
            counts["unresolved_flow_state"] += 1
            return
        target = f"flow_state:{state_key}"
        if state_key not in emitted_states:
            state_index, state_row = state
            emit("flow_state", {"id": target, "state_key": state_key,
                                "source": _source(paths["flow_state"], state_index), "raw": state_row})
            emitted_states.add(state_key)
            counts["flow_state"] += 1
        edge(owner, target, "references_flow_state", basis, source, raw_path,
             {"flow_id": identity, "state_id": state_id})
        counts["flow_references"] += 1

    def link_nested_flows(owner: str, data: Any, source: dict[str, Any]) -> None:
        for raw_path, value in _visit(data):
            if isinstance(value, dict) and {"FlowListName", "FlowId", "StateId"} <= value.keys():
                link_flow(owner, value, source, raw_path, "explicit_flow_triple")

    def explicit_text_keys(data: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for raw_path, value in _visit(data):
            if not isinstance(value, dict):
                continue
            for key, text_key in value.items():
                if key.startswith("Tid") and isinstance(text_key, str) and text_key:
                    item = {"key": text_key, "raw_path": f"{raw_path}.{key}"}
                    if localize:
                        item["localized"] = localize(text_key)
                    found.append(item)
        return found

    for table, kind, prefix, text_fields in (
        ("quest_types", "quest_type", "quest_type", ("QuestTypeName",)),
        ("quest_chapters", "quest_chapter", "quest_chapter",
         ("ChapterNum", "SectionNum", "ActName", "ChapterName")),
        ("quest_tree_chapters", "quest_tree_chapter", "quest_tree_chapter",
         ("Name", "RegionName", "TitleText")),
    ):
        for index, row in enumerate(optional_tables[table]):
            source = _source(paths[table], index)
            identifier = row.get("Id") if isinstance(row, dict) else None
            if not isinstance(identifier, int):
                diagnostic("unsupported_quest_classification_row", "error", source, "$", row)
                continue
            text_keys = []
            for field in text_fields:
                value = row.get(field)
                if isinstance(value, str) and value:
                    entry = {"key": value, "raw_path": field}
                    if localize:
                        entry["localized"] = localize(value)
                    text_keys.append(entry)
            emit(kind, {"id": f"{prefix}:{identifier}", "game_id": identifier,
                        "source": source, "raw": row, "text_keys": text_keys})
            counts[kind] += 1

    for index, row in enumerate(quest_rows):
        source = _source(paths["quest_data"], index)
        quest_id = row.get("QuestId")
        data = _data(row.get("Data"), source, diagnostic)
        if not isinstance(quest_id, int) or not isinstance(data, dict):
            diagnostic("unsupported_quest_row", "error", source, "$", row)
            continue
        owner = f"quest:{quest_id}"
        emit("quest", {"id": owner, "quest_id": quest_id, "source": source,
                       "raw": row, "data": data, "text_keys": explicit_text_keys(data)})
        counts["quest"] += 1
        quest_type = data.get("Type")
        if isinstance(quest_type, int) and quest_type in quest_type_ids:
            edge(owner, f"quest_type:{quest_type}", "has_quest_type", "exact_quest_type_id",
                 source, "Data.Type", None)
            counts["quest_type_links"] += 1
        elif quest_type_ids:
            diagnostic("unresolved_quest_type", "warning", source, "Data.Type", quest_type)
        chapter_id = data.get("ChapterId")
        if isinstance(chapter_id, int) and chapter_id != 0:
            if chapter_id in chapter_ids:
                edge(owner, f"quest_chapter:{chapter_id}", "in_quest_chapter",
                     "exact_chapter_id", source, "Data.ChapterId", None)
                counts["quest_chapter_links"] += 1
            elif chapter_ids:
                diagnostic("unresolved_quest_chapter", "warning", source,
                           "Data.ChapterId", chapter_id)
        link_nested_flows(owner, data, source)
        for raw_path, value in _visit(data.get("ProvideType", {}), "Data.ProvideType"):
            if isinstance(value, dict) and value.get("Type") == "PreQuest":
                previous = value.get("PreQuest")
                if previous in quest_ids:
                    edge(owner, f"quest:{previous}", "requires_quest", "explicit_pre_quest", source,
                         f"{raw_path}.PreQuest", None)
                    counts["prerequisite"] += 1
                else:
                    diagnostic("unresolved_pre_quest", "warning", source, f"{raw_path}.PreQuest", previous)
                    counts["unresolved_pre_quest"] += 1
        # Reference prefixes have source-specific semantics; only q_<QuestId>
        # has a directly resolvable quest identity. Other prefixes stay in raw.
        for ref_index, ref in enumerate(data.get("Reference", [])):
            if isinstance(ref, str) and ref.startswith("q_") and ref[2:].isdigit():
                target_id = int(ref[2:])
                raw_path = f"Data.Reference[{ref_index}]"
                if target_id in quest_ids:
                    edge(owner, f"quest:{target_id}", "references_quest", "explicit_reference_token",
                         source, raw_path, {"token": ref})
                    counts["quest_references"] += 1
                else:
                    diagnostic("unresolved_quest_reference", "warning", source, raw_path, ref)

    for index, row in enumerate(node_rows):
        source = _source(paths["quest_nodes"], index)
        key = row.get("Key")
        data = _data(row.get("Data"), source, diagnostic)
        if not isinstance(key, str) or not isinstance(data, dict) or "_" not in key:
            diagnostic("unsupported_quest_node_row", "error", source, "$", row)
            continue
        quest_part, node_part = key.split("_", 1)
        if not quest_part.isdigit() or not node_part.isdigit():
            diagnostic("unsupported_quest_node_key", "error", source, "Key", key)
            continue
        quest_id, node_id = int(quest_part), int(node_part)
        owner = f"quest_node:{quest_id}:{node_id}"
        emit("quest_node", {"id": owner, "quest_id": quest_id, "node_id": node_id,
                            "node_type": data.get("Type"), "source": source, "raw": row,
                            "data": data, "text_keys": explicit_text_keys(data)})
        counts["quest_node"] += 1
        if data.get("Id") != node_id:
            diagnostic("quest_node_id_mismatch", "error", source, "Data.Id",
                       {"key": key, "data_id": data.get("Id")})
        if quest_id in quest_ids:
            edge(f"quest:{quest_id}", owner, "has_quest_node", "quest_node_key_prefix",
                 source, "Key", None)
        else:
            diagnostic("orphan_quest_node", "error", source, "Key", key)
        parent = data.get("ParentNodeId", 0)
        if isinstance(parent, int) and parent:
            parent_key = f"{quest_id}_{parent}"
            if parent_key in node_ids:
                edge(f"quest_node:{quest_id}:{parent}", owner, "parent_of",
                     "explicit_parent_node_id", source, "Data.ParentNodeId", None)
                counts["parent_edges"] += 1
            else:
                diagnostic("unresolved_parent_node", "error", source, "Data.ParentNodeId", parent)
        link_nested_flows(owner, data, source)
        for raw_path, value in _visit(data):
            if isinstance(value, dict) and value.get("Type") == "CheckChildQuestFinished":
                target_q = value.get("QuestId", quest_id)
                target_n = value.get("ChildQuestId")
                target_key = f"{target_q}_{target_n}"
                if target_key in node_ids:
                    edge(owner, f"quest_node:{target_q}:{target_n}", "checks_quest_node",
                         "explicit_condition_target", source, raw_path, None)
                else:
                    diagnostic("unresolved_condition_node", "warning", source, raw_path, value)
            if isinstance(value, dict) and value.get("Type") == "ConditionSelector":
                for slot_index, slot in enumerate(value.get("Slots", [])):
                    target = slot.get("Node") if isinstance(slot, dict) else None
                    if not isinstance(target, dict) or not isinstance(target.get("Id"), int):
                        diagnostic("unsupported_condition_slot", "warning", source,
                                   f"{raw_path}.Slots[{slot_index}]", slot)
                        continue
                    target_id = target["Id"]
                    if f"{quest_id}_{target_id}" in node_ids:
                        branch_id = f"branch:quest:{quest_id}:row:{index}:{raw_path}:slot:{slot_index}"
                        branch_path = f"{raw_path}.Slots[{slot_index}]"
                        emit("branch", {"id": branch_id, "quest_id": quest_id,
                                        "branch_kind": "condition_selector_slot",
                                        "slot_index": slot_index, "condition": slot.get("Condition"),
                                        "source": {**source, "raw_path": f"$[{index}].{branch_path}"},
                                        "raw": slot})
                        edge(f"quest_node:{quest_id}:{value.get('Id')}", branch_id,
                             "has_condition_branch", "explicit_selector_slot", source,
                             branch_path, {"slot_index": slot_index})
                        edge(branch_id, f"quest_node:{quest_id}:{target_id}",
                             "branch_target", "explicit_selector_slot_node", source,
                             f"{branch_path}.Node", None)
                        edge(f"quest_node:{quest_id}:{value.get('Id')}",
                             f"quest_node:{quest_id}:{target_id}", "condition_slot",
                             "explicit_selector_slot", source, f"{raw_path}.Slots[{slot_index}].Node",
                             {"slot_index": slot_index, "condition": slot.get("Condition")})
                        counts["condition_slots"] += 1
                    else:
                        diagnostic("unresolved_condition_slot_node", "warning", source,
                                   f"{raw_path}.Slots[{slot_index}].Node.Id", target_id)

    for index, row in enumerate(optional_tables["quest_tree_nodes"]):
        source = _source(paths["quest_tree_nodes"], index)
        identifier = row.get("Id") if isinstance(row, dict) else None
        quest_array = row.get("QuestArray") if isinstance(row, dict) else None
        predecessors = row.get("PreNode") if isinstance(row, dict) else None
        if not isinstance(identifier, int) or not isinstance(quest_array, list) or not isinstance(predecessors, list):
            diagnostic("unsupported_quest_tree_node", "error", source, "$", row)
            continue
        owner = f"quest_tree_node:{identifier}"
        text_keys = []
        for field in ("Name", "QuestChapterName", "ChapterName", "Summary", "AccessDesc"):
            value = row.get(field)
            if isinstance(value, str) and value:
                item = {"key": value, "raw_path": field}
                if localize:
                    item["localized"] = localize(value)
                text_keys.append(item)
        emit("quest_tree_node", {"id": owner, "game_id": identifier,
                                 "chapter_id": row.get("ChapterId"), "quest_type": row.get("QuestType"),
                                 "node_type": row.get("NodeType"), "source": source,
                                 "raw": row, "text_keys": text_keys})
        counts["quest_tree_node"] += 1
        chapter_id = row.get("ChapterId")
        if chapter_id in tree_chapter_ids:
            edge(owner, f"quest_tree_chapter:{chapter_id}", "in_quest_tree_chapter",
                 "exact_tree_chapter_id", source, "ChapterId", None)
        else:
            diagnostic("unresolved_quest_tree_chapter", "warning", source, "ChapterId", chapter_id)
        tree_type = row.get("QuestType")
        if tree_type in quest_type_ids:
            edge(owner, f"quest_type:{tree_type}", "has_quest_type",
                 "exact_quest_type_id", source, "QuestType", None)
        elif quest_type_ids:
            diagnostic("unresolved_quest_tree_type", "warning", source, "QuestType", tree_type)
        main_node = row.get("MainQuestNode")
        if isinstance(main_node, int) and main_node:
            if main_node in tree_node_ids:
                edge(owner, f"quest_tree_node:{main_node}", "quest_tree_main_node",
                     "explicit_main_quest_node", source, "MainQuestNode", None)
                counts["quest_tree_main_node_links"] += 1
            else:
                diagnostic("unresolved_quest_tree_main_node", "warning", source,
                           "MainQuestNode", main_node)
        included = row.get("IncludeNodes", [])
        if isinstance(included, list):
            for position, included_node in enumerate(included):
                if included_node in tree_node_ids:
                    edge(owner, f"quest_tree_node:{included_node}", "quest_tree_includes_node",
                         "explicit_include_nodes", source, f"IncludeNodes[{position}]", None)
                    counts["quest_tree_includes_node_links"] += 1
                else:
                    diagnostic("unresolved_quest_tree_included_node", "warning", source,
                               f"IncludeNodes[{position}]", included_node)
        else:
            diagnostic("unsupported_quest_tree_include_nodes", "warning", source,
                       "IncludeNodes", included)
        for position, quest_id in enumerate(quest_array):
            if quest_id in quest_ids:
                edge(owner, f"quest:{quest_id}", "quest_tree_contains_quest",
                     "explicit_quest_array", source, f"QuestArray[{position}]",
                     {"array_index": position, "ordering_scope": "quest_tree_membership_only"})
                counts["quest_tree_quest_links"] += 1
            else:
                diagnostic("unresolved_quest_tree_quest", "warning", source,
                           f"QuestArray[{position}]", quest_id)
        for position, previous in enumerate(predecessors):
            if previous in tree_node_ids:
                edge(owner, f"quest_tree_node:{previous}", "quest_tree_predecessor",
                     "explicit_pre_node", source, f"PreNode[{position}]",
                     {"ordering_scope": "authored_quest_tree", "runtime_traversal": False})
                counts["quest_tree_predecessor_links"] += 1
            else:
                diagnostic("unresolved_quest_tree_predecessor", "warning", source,
                           f"PreNode[{position}]", previous)
        next_node = row.get("NextNode")
        if isinstance(next_node, int) and next_node != 0:
            if next_node in tree_node_ids:
                edge(owner, f"quest_tree_node:{next_node}", "quest_tree_next",
                     "explicit_next_node", source, "NextNode",
                     {"ordering_scope": "authored_quest_tree", "runtime_traversal": False})
                counts["quest_tree_next_links"] += 1
            else:
                diagnostic("unresolved_quest_tree_next", "warning", source, "NextNode", next_node)

    for index, row in enumerate(plot_rows):
        source = _source(paths["plot_handbook"], index)
        quest_id = row.get("QuestId")
        entries = _data(row.get("Data"), source, diagnostic)
        if not isinstance(quest_id, int) or not isinstance(entries, list):
            diagnostic("unsupported_plot_handbook_row", "error", source, "$", row)
            continue
        for step_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                diagnostic("unsupported_plot_step", "warning", source, f"Data[{step_index}]", entry)
                continue
            step_id = f"plot_step:{quest_id}:{step_index}"
            step_source = {**source, "entry_index": step_index,
                           "raw_path": f"$[{index}].Data[{step_index}]"}
            emit("plot_step", {"id": step_id, "quest_id": quest_id, "step_index": step_index,
                               "source": step_source, "raw": entry, "text_keys": explicit_text_keys(entry)})
            counts["plot_step"] += 1
            if quest_id in quest_ids:
                edge(f"quest:{quest_id}", step_id, "has_plot_step", "plot_handbook_quest_id",
                     step_source, "QuestId", None)
            else:
                diagnostic("orphan_plot_step", "warning", step_source, "QuestId", quest_id)
            flow = entry.get("Flow")
            if isinstance(flow, dict) and flow.get("FlowListName"):
                scene_id = f"scene:plot:{quest_id}:{step_index}"
                emit("scene", {"id": scene_id, "quest_id": quest_id, "step_index": step_index,
                               "source": step_source, "raw": entry, "flow": flow})
                edge(step_id, scene_id, "presents_scene", "explicit_plot_step_flow",
                     step_source, f"Data[{step_index}].Flow", None)
                link_flow(scene_id, flow, step_source, f"Data[{step_index}].Flow", "explicit_plot_step_flow")
                counts["scene"] += 1
            if step_index:
                edge(f"plot_step:{quest_id}:{step_index - 1}", step_id, "next_authored_plot_step",
                     "plot_handbook_array_order", step_source, f"Data[{step_index}]",
                     {"ordering_scope": "authored_plot_handbook", "runtime_traversal": False})

    return dict(counts)
