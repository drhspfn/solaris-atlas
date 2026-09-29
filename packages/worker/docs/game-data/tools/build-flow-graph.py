#!/usr/bin/env python3
"""Build a directed graph from mrzjy/WutheringDialog-style action JSON.

The input is the object shown in that project's README: {title, actions:[...]}
or JSONL containing such objects. Original action IDs and option target IDs are
preserved. The source extractor currently emits a flattened action list, so
edges between consecutive actions are explicitly marked inferred-from-list.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def export_flow(flow: dict[str, Any], source: str) -> dict[str, Any]:
    # The canonical extractor already emits the requested nodes/edges shape.
    # Retain its game IDs and exact source references without flattening them.
    if isinstance(flow.get("nodes"), list) and isinstance(flow.get("edges"), list):
        return {key: value for key, value in flow.items()
                if key in {"quest", "scenes", "nodes", "edges", "source_files", "diagnostics"}}
    actions = flow.get("actions", [])
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    known_ids: set[str] = set()

    for action in actions:
        action_id = str(action.get("id", ""))
        aid = f"action:{action_id}"
        known_ids.add(action_id)
        dialogs = action.get("dialogs", [])
        nodes.append({"id": aid,
                      "type": "dialogue_action" if dialogs else "game_action",
                      "source": source,
                      "game_ids": {"ActionId": action.get("id")},
                      "name": action.get("name")})
        previous_dialog_id = None
        for i, dialog in enumerate(dialogs):
            dialog_type = dialog.get("type")
            node_type = {"dialog": "dialogue", "option": "player_choice",
                         "plot": "narration"}.get(dialog_type, "game_action")
            did = f"{node_type}:{action_id}:{i}"
            nodes.append({"id": did, "type": node_type, "source": source,
                          "game_ids": {"ActionId": action.get("id")},
                          "speaker": dialog.get("role"),
                          "text": dialog.get("content")})
            edges.append({"from": aid, "to": did, "type": "contains",
                          "ordering_basis": "source-dialog-list", "source": source})
            if previous_dialog_id is not None:
                edges.append({"from": previous_dialog_id, "to": did,
                              "type": "dialog_sequence",
                              "ordering_basis": "source-dialog-list", "source": source})
            previous_dialog_id = did
            if dialog_type == "option":
                for j, option in enumerate(dialog.get("content", [])):
                    target = option.get("next_action") if isinstance(option, dict) else None
                    if target is not None:
                        edges.append({"from": did, "to": f"action:{target}",
                                      "type": "choice_target", "label": option.get("content"),
                                      "choice_index": j, "source": source})

    for left, right in zip(actions, actions[1:]):
        edges.append({"from": f"action:{left.get('id')}",
                      "to": f"action:{right.get('id')}",
                      "type": "sequence", "ordering_basis": "inferred-from-list",
                      "source": source})

    for edge in edges:
        if edge["type"] == "choice_target":
            target = edge["to"].removeprefix("action:")
            edge["target_present"] = target in known_ids

    return {"quest": {"title": flow.get("title"), "source": source},
            "scenes": [], "nodes": nodes, "edges": edges}


def read_records(path: Path):
    raw = path.read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return value if isinstance(value, list) else [value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = read_records(args.input)
    outputs = [export_flow(record, str(args.input)) for record in records]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = outputs if len(outputs) != 1 else outputs[0]
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
