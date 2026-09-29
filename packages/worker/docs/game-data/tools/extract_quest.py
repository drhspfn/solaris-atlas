#!/usr/bin/env python3
"""Deterministic proof-of-concept extractor for an Arikatsu data checkout."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "upstream" / "WutheringWaves_Data"
VERSION = "3.6.0 (Resource 3.6.6; changelist 8499915)"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_textmap() -> tuple[dict[str, str], dict[str, str]]:
    result: dict[str, str] = {}
    sources: dict[str, str] = {}
    for part in ("multi_text", "multi_text_1sthalf", "multi_text_2ndhalf"):
        path = DATA / "Textmaps" / "en" / part / "MultiText.json"
        for row in read(path):
            if row.get("Id"):
                result[row["Id"]] = row.get("Content", "")
                sources[row["Id"]] = str(path.relative_to(ROOT))
    return result, sources


def nested_actions(rows: list[dict[str, Any]]):
    for row in rows:
        yield row
        yield from nested_actions(row.get("Actions", []))


def extract(quest_id: int) -> dict[str, Any]:
    handbook_path = DATA / "BinData" / "PlotHandBook" / "plothandbookconfig.json"
    state_path = DATA / "BinData" / "flowState" / "flowstate.json"
    flow_path = DATA / "BinData" / "flow" / "flow.json"
    video_path = DATA / "BinData" / "cgVedio" / "videodata.json"
    sound_path = DATA / "BinData" / "cgVedio" / "videosound.json"
    caption_path = DATA / "BinData" / "cgVedio" / "videocaption.json"
    quest_data_path = DATA / "BinData" / "QuestData" / "questdata.json"
    quest_video_path = DATA / "BinData" / "QuestRefVideo" / "questrefvideoconfig.json"
    handbook = next((r for r in read(handbook_path) if r.get("QuestId") == quest_id), None)
    if handbook is None:
        raise SystemExit(f"QuestId {quest_id} not found in {handbook_path}")

    textmap, textmap_sources = load_textmap()
    name_prefix = f"Quest_{quest_id}_QuestName"
    quest_name = next((v for k, v in textmap.items() if k.startswith(name_prefix)), None)
    state_data = read(state_path)
    state_index = {r["StateKey"]: r for r in state_data if r.get("StateKey")}
    plot_steps = json.loads(handbook.get("Data") or "[]")
    flow_rows = read(flow_path)
    flow_index = {r.get("Id"): r for r in flow_rows}
    videos = read(video_path)
    sounds = read(sound_path)
    captions = read(caption_path)
    quest_data_rows = read(quest_data_path)
    quest_metadata = next((r.get("Data", {}) for r in quest_data_rows
                           if r.get("QuestId") == quest_id), {})
    quest_video_refs = [r for r in read(quest_video_path) if r.get("QuestId") == quest_id]

    scenes: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    missing_states: list[str] = []
    previous_state_last: str | None = None
    state_keys: list[str] = []
    action_nodes: dict[tuple[str, int], str] = {}
    talk_nodes: dict[tuple[str, Any, int], str] = {}

    for step_index, step in enumerate(plot_steps):
        flow = step.get("Flow", {})
        list_name = flow.get("FlowListName", "")
        flow_id, state_id = flow.get("FlowId", 0), flow.get("StateId", 0)
        if not list_name:
            continue
        state_key = f"{list_name}_{flow_id}_{state_id}"
        state_keys.append(state_key)
        raw_state = state_index.get(state_key)
        if raw_state is None:
            missing_states.append(state_key)
            continue

        scene_id = f"scene:{step_index}:{state_key}"
        flow_row = flow_index.get(list_name)
        scenes.append({"id": scene_id, "order": len(scenes), "state_key": state_key,
                       "flow_id": flow_id, "state_id": state_id,
                       "flow_record": flow_row,
                       "source": str(state_path.relative_to(ROOT)),
                       "flow_source": str(flow_path.relative_to(ROOT)),
                       "quest_step_source": str(handbook_path.relative_to(ROOT))})
        actions = json.loads(raw_state.get("Actions") or "[]")
        first_node: str | None = None
        last_node: str | None = None

        for action_index, action in enumerate(actions):
            action_id = action.get("ActionId")
            action_name = action.get("Name", "UnknownAction")
            params = action.get("Params", {})
            action_local = f"action:{step_index}:{action_id if action_id is not None else action_index}"
            talk_items = params.get("TalkItems", [])
            is_movie = action_name == "PlayMovie"
            if is_movie:
                node_type = "cutscene"
            elif "quest" in action_name.lower():
                node_type = "quest_transition"
            elif talk_items:
                node_type = "game_action"
            else:
                node_type = "game_action"
            node = {"id": action_local, "canonical_local_id": action_local,
                    "type": node_type, "name": action_name,
                    "source": str(state_path.relative_to(ROOT)),
                    "game_ids": {"QuestId": quest_id, "FlowId": flow_id,
                                 "StateId": state_id, "ActionId": action_id},
                    "version": VERSION}
            if is_movie:
                video_name = params.get("VideoName")
                video_rows = [v for v in videos if v.get("CgName") == video_name]
                sound_rows = [v for v in sounds if v.get("CgName") == video_name]
                caption_rows = [v for v in captions if v.get("CgName") == video_name]
                node["cutscene"] = {"video_name": video_name,
                                    "video_assets": video_rows,
                                    "audio_events": sound_rows,
                                    "captions": [{**c, "text": textmap.get(c.get("CaptionText"), ""),
                                                  "text_source": textmap_sources.get(c.get("CaptionText"))}
                                                 for c in caption_rows],
                                    "source_files": [str(p.relative_to(ROOT)) for p in
                                                     (video_path, sound_path, caption_path)]}
            nodes.append(node)
            action_nodes[(state_key, action_index)] = action_local
            first_node = first_node or action_local
            if last_node:
                edges.append({"from": last_node, "to": action_local, "type": "action_sequence",
                              "ordering_basis": "FlowState.Actions array",
                              "source": str(state_path.relative_to(ROOT))})
            last_node = action_local
            edges.append({"from": scene_id, "to": action_local, "type": "contains"})

            if not talk_items:
                continue
            items_by_id = {i.get("Id"): i for i in talk_items if i.get("Id") is not None}
            talk_sequence = params.get("TalkSequence")
            if talk_sequence:
                ordered_ids = [i for seq in talk_sequence for i in seq]
                ordered_items = [items_by_id[i] for i in ordered_ids if i in items_by_id]
                order_basis = "TalkSequence"
            else:
                ordered_items = talk_items
                order_basis = "TalkItems array (no TalkSequence)"

            local_talk_ids: dict[int, str] = {}
            option_nodes_by_key: dict[str, str] = {}
            pending_jump_links: list[tuple[str, int, Any]] = []
            for talk_index, talk in enumerate(ordered_items):
                talk_id = talk.get("Id", talk_index)
                tid = talk.get("TidTalk")
                who_id = talk.get("WhoId")
                source_talk_type = talk.get("Type", "")
                if source_talk_type in {"Option", "SystemOption", "QTE"}:
                    talk_type = "player_choice"
                elif source_talk_type in {"CenterText", "AvgCenterText", "AvgNarration", "NoTextItem"}:
                    talk_type = "narration"
                else:
                    talk_type = "dialogue" if who_id is not None else "narration"
                talk_local = f"{talk_type}:{step_index}:{action_id}:{talk_id}"
                local_talk_ids[talk_id] = talk_local
                talk_nodes[(state_key, action_id, talk_id)] = talk_local
                node = {"id": talk_local, "canonical_local_id": talk_local,
                        "type": talk_type, "source": str(state_path.relative_to(ROOT)),
                        "source_type": source_talk_type,
                        "speaker": {"id": who_id,
                                    "text_id": f"Speaker_{who_id}_Name" if who_id is not None else None,
                                    "name": textmap.get(f"Speaker_{who_id}_Name", "") if who_id is not None else None,
                                    "source": textmap_sources.get(f"Speaker_{who_id}_Name") if who_id is not None else None},
                        "text": {"id": tid, "content": textmap.get(tid, "") if tid else None,
                                 "source": textmap_sources.get(tid) if tid else None},
                        "game_ids": {"QuestId": quest_id, "FlowId": flow_id,
                                     "StateId": state_id, "ActionId": action_id,
                                     "TalkId": talk_id, "TextId": talk.get("TextId"),
                                     "SpeakerId": who_id},
                        "version": VERSION, "ordering_basis": order_basis}
                if talk.get("Name"):
                    node["source_name"] = talk["Name"]
                nodes.append(node)
                edges.append({"from": action_local, "to": talk_local, "type": "contains"})
                for choice_index, option in enumerate(talk.get("Options", [])):
                    choice_local = f"player_choice:{step_index}:{action_id}:{talk_id}:{choice_index}"
                    option_key = option.get("TidTalkOption")
                    nodes.append({"id": choice_local, "canonical_local_id": choice_local,
                                  "type": "player_choice", "source": str(state_path.relative_to(ROOT)),
                                  "speaker": {"id": None, "name": "Player"},
                                  "text": {"id": option_key, "content": textmap.get(option_key, ""),
                                           "source": textmap_sources.get(option_key)},
                                  "game_ids": {"QuestId": quest_id, "FlowId": flow_id,
                                               "StateId": state_id, "ActionId": action_id,
                                               "TalkId": talk_id, "TextId": option.get("TextId")},
                                  "version": VERSION})
                    edges.append({"from": talk_local, "to": choice_local,
                                  "type": "presents_choice", "choice_index": choice_index})
                    if option_key:
                        option_nodes_by_key[option_key] = choice_local
                    for nested in nested_actions(option.get("Actions", [])):
                        if nested.get("Name") == "JumpTalk":
                            target_id = nested.get("Params", {}).get("TalkId")
                            pending_jump_links.append((choice_local, target_id, nested.get("ActionId")))
                        else:
                            nested_local = f"action:{step_index}:{action_id}:{talk_id}:choice:{choice_index}:{nested.get('ActionId', len(nodes))}"
                            nodes.append({"id": nested_local, "canonical_local_id": nested_local,
                                          "type": "quest_transition" if "quest" in nested.get("Name", "").lower() else "game_action",
                                          "name": nested.get("Name"), "source": str(state_path.relative_to(ROOT)),
                                          "game_ids": {"QuestId": quest_id, "FlowId": flow_id,
                                                       "StateId": state_id, "ActionId": nested.get("ActionId")},
                                          "version": VERSION})
                            edges.append({"from": choice_local, "to": nested_local,
                                          "type": "choice_action"})

                for nested in nested_actions(talk.get("Actions", [])):
                    nested_local = f"action:{step_index}:{action_id}:{talk_id}:talk:{nested.get('ActionId', len(nodes))}"
                    if nested.get("Name") == "JumpTalk":
                        pending_jump_links.append((talk_local, nested.get("Params", {}).get("TalkId"), nested.get("ActionId")))
                    else:
                        nodes.append({"id": nested_local, "canonical_local_id": nested_local,
                                      "type": "quest_transition" if "quest" in nested.get("Name", "").lower() else "game_action",
                                      "name": nested.get("Name"), "source": str(state_path.relative_to(ROOT)),
                                      "game_ids": {"QuestId": quest_id, "FlowId": flow_id,
                                                   "StateId": state_id, "ActionId": nested.get("ActionId")},
                                      "version": VERSION})
                        edges.append({"from": talk_local, "to": nested_local, "type": "talk_action"})

            for source_local, target_id, nested_action_id in pending_jump_links:
                target_local = local_talk_ids.get(target_id)
                if target_local:
                    edges.append({"from": source_local, "to": target_local,
                                  "type": "talk_jump", "game_ids":
                                  {"ActionId": nested_action_id, "TalkId": target_id}})

            for seq_index, sequence in enumerate(talk_sequence or []):
                for left, right in zip(sequence, sequence[1:]):
                    if left in local_talk_ids and right in local_talk_ids:
                        edges.append({"from": local_talk_ids[left], "to": local_talk_ids[right],
                                      "type": "dialogue_sequence", "ordering_basis": "TalkSequence"})
            transitions = params.get("SequenceTransitions", {})
            for seq_key, seq_transitions in transitions.items():
                try:
                    source_seq = int(seq_key)
                except (TypeError, ValueError):
                    continue
                if not talk_sequence or source_seq >= len(talk_sequence) or not talk_sequence[source_seq]:
                    continue
                source_id = talk_sequence[source_seq][-1]
                source_local = local_talk_ids.get(source_id)
                for transition_index, transition in enumerate(seq_transitions):
                    target_seq = transition.get("NextSequenceIndex")
                    if target_seq is None or target_seq >= len(talk_sequence) or not talk_sequence[target_seq]:
                        continue
                    target_local = local_talk_ids.get(talk_sequence[target_seq][0])
                    if not source_local or not target_local:
                        continue
                    label_key = transition.get("OptionTextKey")
                    if label_key:
                        choice_local = option_nodes_by_key.get(label_key)
                        if choice_local is None:
                            choice_local = f"transition_choice:{step_index}:{action_id}:{source_seq}:{transition_index}"
                            nodes.append({"id": choice_local, "canonical_local_id": choice_local,
                                          "type": "player_choice", "source": str(state_path.relative_to(ROOT)),
                                          "speaker": {"id": None, "name": "Player"},
                                          "text": {"id": label_key,
                                                   "content": textmap.get(label_key, transition.get("OptionText", "")),
                                                   "source": textmap_sources.get(label_key)},
                                          "game_ids": {"QuestId": quest_id, "FlowId": flow_id,
                                                       "StateId": state_id, "ActionId": action_id},
                                          "version": VERSION})
                            edges.append({"from": source_local, "to": choice_local,
                                          "type": "presents_choice"})
                        edges.append({"from": choice_local, "to": target_local,
                                      "type": "choice_branch", "game_ids":
                                      {"SequenceIndex": target_seq}})
                    else:
                        edges.append({"from": source_local, "to": target_local,
                                      "type": "sequence_transition", "game_ids":
                                      {"SequenceIndex": target_seq}})

        if previous_state_last and first_node:
            edges.append({"from": previous_state_last, "to": first_node,
                          "type": "quest_sequence", "ordering_basis": "PlotHandBook.Data array"})
        if last_node:
            previous_state_last = last_node

    # Map explicit action-level JumpTalk links once all talk nodes are known.
    for state_key in dict.fromkeys(state_keys):
        raw_state = state_index.get(state_key)
        if raw_state is None:
            continue
        for action_index, action in enumerate(json.loads(raw_state.get("Actions") or "[]")):
            for talk in action.get("Params", {}).get("TalkItems", []):
                for nested in nested_actions(talk.get("Actions", [])):
                    if nested.get("Name") != "JumpTalk":
                        continue
                    target = nested.get("Params", {}).get("TalkId")
                    src_id = talk_nodes.get((state_key, action.get("ActionId"), talk.get("Id")))
                    dst_id = talk_nodes.get((state_key, action.get("ActionId"), target))
                    if src_id and dst_id:
                        edges.append({"from": src_id, "to": dst_id, "type": "talk_jump",
                                      "source": str(state_path.relative_to(ROOT)),
                                      "game_ids": {"ActionId": nested.get("ActionId"), "TalkId": target}})

    return {"quest": {"id": quest_id, "name": quest_name,
                       "source": str(handbook_path.relative_to(ROOT)),
                       "name_source": textmap_sources.get(next((k for k in textmap if k.startswith(name_prefix)), "")),
                       "metadata": quest_metadata,
                       "quest_video_refs": quest_video_refs,
                       "metadata_source": str(quest_data_path.relative_to(ROOT)),
                       "quest_video_source": str(quest_video_path.relative_to(ROOT)),
                       "version": VERSION},
            "scenes": scenes, "nodes": nodes, "edges": edges,
            "source_files": [str(p.relative_to(ROOT)) for p in
                             (handbook_path, state_path, flow_path, quest_data_path, quest_video_path,
                              video_path, sound_path, caption_path)],
            "diagnostics": {"plot_steps": len(plot_steps), "unique_state_keys": len(set(state_keys)),
                            "resolved_scenes": len(scenes), "missing_state_keys": missing_states,
                            "ordering_note": "Raw array/explicit sequence links are retained. Unresolved sequence semantics are not inferred."}}


def write_dot(graph: dict[str, Any], path: Path) -> None:
    labels = {n["id"]: f"{n['type']}\\n{n.get('name') or n.get('text', {}).get('content') or n['id']}"
              for n in graph["nodes"]}
    lines = ["digraph quest {", "  rankdir=TB;"]
    for node_id, label in labels.items():
        safe = label.replace('"', '\\"')[:180]
        lines.append(f'  "{node_id}" [label="{safe}"];')
    for edge in graph["edges"]:
        lines.append(f'  "{edge["from"]}" -> "{edge["to"]}" [label="{edge["type"]}"];')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("quest_id", type=int)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out = args.out or ROOT / "output" / "canonical" / f"{args.quest_id}.json"
    graph = extract(args.quest_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_dot(graph, out.with_suffix(".dot"))
    print(f"Wrote {out} ({len(graph['scenes'])} scenes, {len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")


if __name__ == "__main__":
    main()
