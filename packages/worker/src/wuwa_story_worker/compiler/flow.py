"""Compile every FlowState action and TalkItem without assuming quest ownership."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .core import Writer, read_json

KNOWN_TALK_TYPES = {"Talk", "Option", "SystemOption", "CenterText", "AvgTalk",
                    "AvgNarration", "AvgCenterText", "PhoneMessage", "QTE", "NoTextItem", ""}
CHOICE_TYPES = {"Option", "SystemOption", "QTE"}
NARRATION_TYPES = {"CenterText", "AvgNarration", "AvgCenterText"}


def cutscene_transcript_links(states, captions):
    """Match complete authored dialogue to video subtitles by localization identity.

    No title, asset-name prefix, translated text, or flow proximity matching.
    Partial matches cannot make unrelated dialogue part of a quest.
    """
    groups = {}
    for index, caption in enumerate(captions):
        name, key = caption.get("CgName"), caption.get("CaptionText")
        if isinstance(name, str) and isinstance(key, str) and key:
            groups.setdefault(name, []).append((index, key))
    by_keys = {}
    for name, entries in groups.items():
        signature = tuple(sorted(key for _, key in entries))
        by_keys.setdefault(signature, []).append((name, [i for i, _ in entries]))
    for index, state in enumerate(states):
        try:
            actions = json.loads(state.get("Actions") or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(actions, list):
            continue
        items = []
        for action in actions:
            params = action.get("Params") if isinstance(action, dict) else None
            talk = params.get("TalkItems") if isinstance(params, dict) else None
            if isinstance(talk, list):
                items.extend(item for item in talk if isinstance(item, dict))
        keys = [item.get("TidTalk") for item in items]
        if not keys or not all(isinstance(key, str) and key for key in keys):
            continue
        for name, caption_rows in by_keys.get(tuple(sorted(keys)), []):
            yield name, state["StateKey"], index, caption_rows


def _nested_actions(actions: Any, prefix: str):
    if not isinstance(actions, list):
        return
    for index, action in enumerate(actions):
        path = f"{prefix}[{index}]"
        yield path, action
        if isinstance(action, dict):
            yield from _nested_actions(action.get("Actions"), f"{path}.Actions")


def compile_flow(root: Path, writer: Writer, english: dict[str, dict[str, Any]],
                 known_action_names: set[str] | None = None) -> dict[str, int]:
    source = "BinData/flowState/flowstate.json"
    rows = read_json(root / source)
    captions_path = root / "BinData/cgVedio/videocaption.json"
    captions = read_json(captions_path) if captions_path.is_file() else []
    for cg, state_key, row, caption_rows in cutscene_transcript_links(rows, captions):
        writer.edge(f"cutscene:{cg}", f"flow_state:{state_key}", "has_transcript_state",
                    f"complete CaptionText = TidTalk multiset; videocaption.json rows {caption_rows}",
                    source, f"$[{row}].Actions",
                    {"relation": "exact_join", "caption_source": "BinData/cgVedio/videocaption.json",
                     "caption_rows": caption_rows})
    flow_source = "BinData/flow/flow.json"
    flows = read_json(root / flow_source)
    flow_by_id = {flow.get("Id"): flow for flow in flows if isinstance(flow, dict)}
    for flow_index, flow in enumerate(flows):
        flow_id = flow.get("Id")
        if not isinstance(flow_id, str) or not flow_id:
            writer.diagnostic("unsupported_flow", "error", flow_source, f"$[{flow_index}]", flow)
            continue
        writer.emit("flow", {"id": f"flow:{flow_id}", "flow_id": flow_id,
                             "source": {"file": flow_source, "row": flow_index,
                                        "raw_path": f"$[{flow_index}]"}, "raw": flow})
    speaker_ids = {r.get("Id") for r in read_json(root / "BinData/speaker/speaker.json")}
    video_names = {r.get("CgName") for r in read_json(root / "BinData/cgVedio/videodata.json")}
    counts: Counter[str] = Counter()
    state_ids = set(writer.ids)
    referenced_voice_ids: set[str] = set()

    def localized(key: Any, path: str) -> dict[str, Any] | None:
        if not isinstance(key, str) or not key:
            return None
        found = english.get(key)
        if found is None:
            writer.diagnostic("unresolved_localization", "warning", source, path, key)
        return {"key": key, "en": found.get("content") if found else None,
                "source": found.get("source") if found else None,
                "resolution": found.get("resolution", "resolved_nonempty") if found else "missing_key"}

    def ensure_asset(path: Any, state_source: str, raw_path: str) -> str | None:
        if not isinstance(path, str) or not path:
            return None
        identifier = f"asset:ue:{path}"
        if identifier not in writer.ids:
            writer.emit("asset_reference", {"id": identifier, "namespace": "ue", "value": path,
                                            "source": {"file": state_source, "raw_path": raw_path},
                                            "raw": {"path": path}})
        return identifier

    def compile_nested(owner: str, actions: Any, path: str, state_key: str) -> list[tuple[str, Any]]:
        jumps: list[tuple[str, Any]] = []
        for raw_path, action in _nested_actions(actions, path):
            if not isinstance(action, dict):
                writer.diagnostic("unsupported_nested_action", "warning", source, raw_path, action)
                continue
            identifier = f"nested_action:{state_key}:{raw_path}"
            writer.emit("nested_action", {"id": identifier, "name": action.get("Name"),
                                           "action_id": action.get("ActionId"),
                                           "source": {"file": source, "raw_path": raw_path}, "raw": action})
            writer.edge(owner, identifier, "contains_nested_action", "nested Actions array", source, raw_path)
            if action.get("Name") == "JumpTalk":
                jumps.append((identifier, (action.get("Params") or {}).get("TalkId")))
        return jumps

    for row_index, row in enumerate(rows):
        state_key = row.get("StateKey")
        if not isinstance(state_key, str) or not state_key:
            writer.diagnostic("unsupported_flow_state", "error", source, f"$[{row_index}]", row)
            continue
        state_id = f"flow_state:{state_key}"
        if state_id not in state_ids:
            writer.emit("flow_state", {"id": state_id, "state_key": state_key,
                                       "source": {"file": source, "row": row_index,
                                                  "raw_path": f"$[{row_index}]"}, "raw": row})
            state_ids.add(state_id)
        flow_name = state_key.rsplit("_", 1)[0]
        if f"flow:{flow_name}" in writer.ids and row.get("Id") in (flow_by_id[flow_name].get("States") or []):
            writer.edge(f"flow:{flow_name}", state_id, "has_flow_state", "StateKey prefix and Flow.States",
                        source, f"$[{row_index}].StateKey")
        else:
            writer.diagnostic("unresolved_flow_membership", "warning", source,
                              f"$[{row_index}].StateKey", {"flow": flow_name, "state_id": row.get("Id")})
        try:
            actions = json.loads(row.get("Actions") or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            writer.diagnostic("invalid_actions_json", "error", source, f"$[{row_index}].Actions", str(exc))
            continue
        if not isinstance(actions, list):
            writer.diagnostic("unsupported_actions_schema", "error", source, f"$[{row_index}].Actions", actions)
            continue
        previous_action: str | None = None
        for action_index, action in enumerate(actions):
            action_path = f"$[{row_index}].Actions<json>[{action_index}]"
            if not isinstance(action, dict):
                writer.diagnostic("unsupported_action", "error", source, action_path, action)
                continue
            action_id = f"action:{state_key}:{action_index}"
            name = action.get("Name")
            if not name or (known_action_names is not None and name not in known_action_names):
                writer.diagnostic("unknown_action_name", "warning", source, f"{action_path}.Name", name)
            writer.emit("action", {"id": action_id, "state_key": state_key,
                                   "action_index": action_index, "action_id": action.get("ActionId"),
                                   "action_guid": action.get("ActionGuid"), "name": name,
                                   "source": {"file": source, "row": row_index, "raw_path": action_path,
                                              "array_index": action_index}, "raw": action})
            writer.edge(state_id, action_id, "contains_action", "Actions array position", source, action_path)
            if previous_action:
                writer.edge(previous_action, action_id, "next_source_action", "Actions array adjacency",
                            source, action_path, {"runtime_traversal": False})
            previous_action = action_id
            counts["actions"] += 1
            params = action.get("Params") or {}
            if not isinstance(params, dict):
                writer.diagnostic("unsupported_action_params", "warning", source, f"{action_path}.Params", params)
                continue

            if name == "PlayMovie":
                cg = params.get("VideoName")
                if cg in video_names:
                    writer.edge(action_id, f"cutscene:{cg}", "plays_cutscene", "VideoName = CgName",
                                source, f"{action_path}.Params.VideoName")
                    counts["resolved_cutscenes"] += 1
                elif isinstance(cg, str) and cg.strip() in video_names and sum(
                        candidate.strip() == cg.strip() for candidate in video_names
                        if isinstance(candidate, str)) == 1:
                    writer.edge(action_id, f"cutscene:{cg.strip()}", "plays_cutscene",
                                "unique whitespace-normalized VideoName = CgName",
                                source, f"{action_path}.Params.VideoName",
                                {"raw_value": cg, "normalized_value": cg.strip(),
                                 "resolution": "unique_whitespace_normalization"})
                    counts["resolved_cutscenes"] += 1
                else:
                    counts["unresolved_cutscenes"] += 1
                    writer.diagnostic("unresolved_cutscene", "warning", source,
                                      f"{action_path}.Params.VideoName", cg)
            if name == "PlaySequenceData":
                sequence_path = params.get("Path")
                asset = ensure_asset(sequence_path, source, f"{action_path}.Params.Path")
                if asset:
                    writer.edge(action_id, asset, "plays_sequence_asset", "Params.Path", source,
                                f"{action_path}.Params.Path")
            if name == "PostAkEvent":
                event = (params.get("EventConfig") or {}).get("AkEvent")
                asset = ensure_asset(event, source, f"{action_path}.Params.EventConfig.AkEvent")
                if asset:
                    writer.edge(action_id, asset, "posts_audio_event", "EventConfig.AkEvent", source,
                                f"{action_path}.Params.EventConfig.AkEvent")
                else:
                    writer.diagnostic("unresolved_audio_event", "warning", source,
                                      f"{action_path}.Params.EventConfig.AkEvent", event)
            if name == "SetAudioState":
                counts["audio_state_actions"] += 1

            items = params.get("TalkItems")
            if items is None:
                continue
            if not isinstance(items, list):
                writer.diagnostic("unsupported_talk_items_schema", "error", source,
                                  f"{action_path}.Params.TalkItems", items)
                continue
            by_local_id: dict[Any, list[str]] = {}
            choice_by_key: dict[str, list[str]] = {}
            pending_jumps: list[tuple[str, Any, str]] = []
            for talk_index, talk in enumerate(items):
                talk_path = f"{action_path}.Params.TalkItems[{talk_index}]"
                if not isinstance(talk, dict):
                    writer.diagnostic("unsupported_talk_item", "error", source, talk_path, talk)
                    continue
                counts["raw_talk_items"] += 1
                typ = talk.get("Type") or ""
                if typ not in KNOWN_TALK_TYPES:
                    writer.diagnostic("unknown_talk_item_type", "warning", source, f"{talk_path}.Type", typ)
                kind = ("phone_message" if typ == "PhoneMessage" else
                        "player_choice" if typ in CHOICE_TYPES else
                        "narration" if typ in NARRATION_TYPES else "talk_item")
                talk_id = f"{kind}:{state_key}:{action_index}:{talk_index}"
                local_id = talk.get("Id")
                if local_id is not None:
                    by_local_id.setdefault(local_id, []).append(talk_id)
                who = talk.get("WhoId")
                text_key = talk.get("TidTalk")
                record = {"id": talk_id, "source_type": typ or None,
                          "state_key": state_key, "action_index": action_index,
                          "talk_index": talk_index, "talk_id": local_id,
                          "text_id": talk.get("TextId"), "localization": localized(text_key, f"{talk_path}.TidTalk"),
                          "speaker_id": who,
                          "source": {"file": source, "row": row_index, "raw_path": talk_path,
                                     "array_index": talk_index}, "raw": talk}
                writer.emit(kind, record)
                writer.edge(action_id, talk_id, "presents_talk_item", "TalkItems array position", source, talk_path)
                counts[kind] += 1
                if who is not None:
                    if who in speaker_ids:
                        writer.edge(talk_id, f"speaker:{who}", "spoken_by", "WhoId = Speaker.Id",
                                    source, f"{talk_path}.WhoId")
                    else:
                        writer.diagnostic("unresolved_speaker", "warning", source, f"{talk_path}.WhoId", who)
                if talk.get("PlayVoice") and isinstance(text_key, str):
                    voice_id = f"voice_ref:{text_key}"
                    if voice_id in writer.ids:
                        writer.edge(talk_id, voice_id, "has_voice_reference", "TidTalk = PlotAudio.Id",
                                    source, f"{talk_path}.TidTalk")
                        counts["voiced_talk_items"] += 1
                        referenced_voice_ids.add(voice_id)
                    else:
                        writer.diagnostic("unresolved_voice_reference", "warning", source,
                                          f"{talk_path}.TidTalk", text_key)
                ak_event = (talk.get("TalkAkEvent") or {}).get("AkEvent")
                asset = ensure_asset(ak_event, source, f"{talk_path}.TalkAkEvent.AkEvent")
                if asset:
                    writer.edge(talk_id, asset, "posts_audio_event", "TalkAkEvent.AkEvent",
                                source, f"{talk_path}.TalkAkEvent.AkEvent")
                for nested_id, target in compile_nested(talk_id, talk.get("Actions"), f"{talk_path}.Actions", state_key):
                    if target is not None:
                        pending_jumps.append((nested_id, target, f"{talk_path}.Actions"))
                options = talk.get("Options") or []
                if not isinstance(options, list):
                    writer.diagnostic("unsupported_options_schema", "error", source, f"{talk_path}.Options", options)
                    continue
                for option_index, option in enumerate(options):
                    option_path = f"{talk_path}.Options[{option_index}]"
                    if not isinstance(option, dict):
                        writer.diagnostic("unsupported_option", "error", source, option_path, option)
                        continue
                    choice_id = f"choice:{state_key}:{action_index}:{talk_index}:{option_index}"
                    key = option.get("TidTalkOption")
                    writer.emit("player_choice", {"id": choice_id, "state_key": state_key,
                                                  "action_index": action_index,
                                                  "talk_index": talk_index, "choice_index": option_index,
                                                  "text_id": option.get("TextId"),
                                                  "localization": localized(key, f"{option_path}.TidTalkOption"),
                                                  "source": {"file": source, "row": row_index,
                                                             "raw_path": option_path, "array_index": option_index},
                                                  "raw": option})
                    writer.edge(talk_id, choice_id, "presents_choice", "Options array position", source, option_path)
                    if isinstance(key, str):
                        choice_by_key.setdefault(key, []).append(choice_id)
                    for nested_id, target in compile_nested(choice_id, option.get("Actions"),
                                                             f"{option_path}.Actions", state_key):
                        if target is not None:
                            pending_jumps.append((nested_id, target, f"{option_path}.Actions"))
                    counts["player_choice"] += 1
                    counts["inline_options"] += 1

            for local_id, ids in by_local_id.items():
                if len(ids) > 1:
                    writer.diagnostic("duplicate_local_talk_id", "warning", source,
                                      f"{action_path}.Params.TalkItems", {"id": local_id, "nodes": ids})
            for nested_id, target, jump_path in pending_jumps:
                matches = by_local_id.get(target, [])
                if len(matches) == 1:
                    writer.edge(nested_id, matches[0], "jumps_to_talk", "JumpTalk.Params.TalkId",
                                source, jump_path)
                else:
                    writer.diagnostic("unresolved_choice_target", "warning", source, jump_path,
                                      {"talk_id": target, "matches": matches})

            sequences = params.get("TalkSequence") or []
            if sequences and not isinstance(sequences, list):
                writer.diagnostic("unsupported_talk_sequence", "error", source,
                                  f"{action_path}.Params.TalkSequence", sequences)
                sequences = []
            for seq_index, sequence in enumerate(sequences):
                if not isinstance(sequence, list):
                    writer.diagnostic("unsupported_talk_sequence", "error", source,
                                      f"{action_path}.Params.TalkSequence[{seq_index}]", sequence)
                    continue
                for pos, (left, right) in enumerate(zip(sequence, sequence[1:])):
                    left_ids, right_ids = by_local_id.get(left, []), by_local_id.get(right, [])
                    seq_path = f"{action_path}.Params.TalkSequence[{seq_index}][{pos}]"
                    if len(left_ids) == len(right_ids) == 1:
                        writer.edge(left_ids[0], right_ids[0], "next_authored_talk", "TalkSequence",
                                    source, seq_path, {"sequence_index": seq_index})
                    else:
                        writer.diagnostic("unresolved_talk_sequence", "warning", source, seq_path,
                                          {"from": left, "to": right, "from_matches": left_ids,
                                           "to_matches": right_ids})
            transitions = params.get("SequenceTransitions") or {}
            if not isinstance(transitions, dict):
                writer.diagnostic("unsupported_sequence_transitions", "error", source,
                                  f"{action_path}.Params.SequenceTransitions", transitions)
                continue
            for source_seq, transition_list in transitions.items():
                try:
                    source_sequence = sequences[int(source_seq)]
                except (ValueError, IndexError, TypeError):
                    writer.diagnostic("unresolved_sequence_transition", "warning", source,
                                      f"{action_path}.Params.SequenceTransitions.{source_seq}", source_seq)
                    continue
                if not isinstance(source_sequence, list) or not source_sequence:
                    continue
                from_matches = by_local_id.get(source_sequence[-1], [])
                if not isinstance(transition_list, list):
                    writer.diagnostic("unsupported_sequence_transitions", "error", source,
                                      f"{action_path}.Params.SequenceTransitions.{source_seq}", transition_list)
                    continue
                for trans_index, transition in enumerate(transition_list):
                    trans_path = f"{action_path}.Params.SequenceTransitions.{source_seq}[{trans_index}]"
                    if not isinstance(transition, dict):
                        writer.diagnostic("unsupported_sequence_transition", "error", source, trans_path, transition)
                        continue
                    branch_id = f"branch:{state_key}:{action_index}:{source_seq}:{trans_index}"
                    writer.emit("branch", {"id": branch_id, "state_key": state_key,
                                           "source_sequence_index": source_seq,
                                           "transition_index": trans_index,
                                           "target_sequence_index": transition.get("NextSequenceIndex"),
                                           "option_text_key": transition.get("OptionTextKey"),
                                           "source": {"file": source, "row": row_index,
                                                      "raw_path": trans_path}, "raw": transition})
                    target_seq = transition.get("NextSequenceIndex")
                    try:
                        target_sequence = sequences[target_seq]
                    except (IndexError, TypeError):
                        target_sequence = []
                    target_matches = by_local_id.get(target_sequence[0], []) if isinstance(target_sequence, list) and target_sequence else []
                    if len(from_matches) != 1 or len(target_matches) != 1:
                        writer.diagnostic("unresolved_sequence_transition", "warning", source, trans_path,
                                          {"from": source_seq, "to": target_seq})
                        continue
                    writer.edge(from_matches[0], branch_id, "has_branch", "SequenceTransitions entry",
                                source, trans_path)
                    writer.edge(branch_id, target_matches[0], "branch_target", "NextSequenceIndex",
                                source, trans_path)
                    key = transition.get("OptionTextKey")
                    if key:
                        options = choice_by_key.get(key, [])
                        if len(options) == 1:
                            choice_id = options[0]
                        else:
                            choice_id = f"transition_choice:{state_key}:{action_index}:{source_seq}:{trans_index}"
                            writer.emit("player_choice", {"id": choice_id, "state_key": state_key,
                                                          "localization": localized(key, f"{trans_path}.OptionTextKey"),
                                                          "source": {"file": source, "row": row_index,
                                                                     "raw_path": trans_path}, "raw": transition})
                        writer.edge(from_matches[0], choice_id, "presents_choice", "OptionTextKey",
                                    source, trans_path)
                        writer.edge(choice_id, target_matches[0], "choice_branch", "NextSequenceIndex",
                                    source, trans_path, {"sequence_index": target_seq})
                    else:
                        writer.edge(from_matches[0], target_matches[0], "sequence_transition",
                                    "NextSequenceIndex", source, trans_path,
                                    {"sequence_index": target_seq})
    counts["referenced_voice_ids"] = len(referenced_voice_ids)
    return dict(counts)
