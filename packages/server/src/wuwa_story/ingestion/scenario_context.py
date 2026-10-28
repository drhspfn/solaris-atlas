"""Conservative scenario context inferred from unique quest progress variables."""
import json
from collections import defaultdict

from wuwa_story.ingestion.dungeon_context import decoded, walk


def scenario_links(quest_rows, level_rows, valid_states):
    """Return contextual links, not claims about runtime traversal or quest ownership."""
    setters = defaultdict(set)
    quest_flows = defaultdict(set)
    evidence = {}
    for index, row in quest_rows:
        key = row.get("Key", "")
        quest, sep, node = key.partition("_")
        if not sep or not quest.isdecimal() or not node.isdecimal():
            continue
        data = decoded(row.get("Data"))
        for path, obj in walk(data, f"$[{index}].Data"):
            if isinstance(obj.get("FlowListName"), str) and type(obj.get("FlowId")) is int:
                quest_flows[quest].add(obj["FlowListName"])
            if obj.get("Name") != "SetVar" or not isinstance(obj.get("Params"), dict):
                continue
            var = obj["Params"].get("VarLeft") or {}
            if isinstance(var, dict) and var.get("Source") == "Global" and isinstance(var.get("Keyword"), str):
                setters[var["Keyword"]].add(quest)
                evidence.setdefault((var["Keyword"], quest), (index, path))

    levels = defaultdict(list)
    for index, row in level_rows:
        key = row.get("Key", "")
        level, sep, node = key.partition("_")
        data = decoded(row.get("Data"))
        if not sep or not level.isdecimal() or not node.isdecimal() or not isinstance(data, dict) or data.get("Id") != int(node):
            continue
        levels[level].append((index, data))

    for level, rows in levels.items():
        variables = set()
        flows = []
        for index, data in rows:
            for path, obj in walk(data, f"$[{index}].Data"):
                if obj.get("Type") == "CompareVar":
                    for _, var in walk(obj):
                        if var.get("Source") == "Global" and isinstance(var.get("Keyword"), str):
                            variables.add(var["Keyword"])
                name, flow, state = (obj.get(k) for k in (
                    "FlowListName", "FlowId", "StateId"))
                if isinstance(name, str) and type(flow) is int and type(state) is int:
                    state_key = f"{name}_{flow}_{state}"
                    if state_key in valid_states:
                        flows.append((name, state_key, index, path))
        candidates = {(variable, next(iter(setters[variable]))) for variable in variables
                      if len(setters[variable]) == 1}
        candidates = {(var, quest) for var, quest in candidates
                      if any(name in quest_flows[quest] for name, *_ in flows)}
        if len({quest for _, quest in candidates}) != 1:
            continue
        for variable, quest in sorted(candidates):
            quest_row, quest_path = evidence[(variable, quest)]
            for name, state_key, index, path in flows:
                if name not in quest_flows[quest]:
                    continue
                yield {"from": f"quest:{quest}", "to": f"flow_state:{state_key}",
                       "source": "BinData/LevelPlayNodeData/levelplaynodedata.json",
                       "raw_path": path, "basis": json.dumps({
                           "meaning": "Inferred scenario context, not proof of runtime traversal",
                           "global_variable": variable, "level_play_id": level,
                           "quest_setter": {"file": "BinData/QuestNodeData/questnodedata.json",
                                            "row": quest_row, "path": quest_path},
                           "flow_source_row": index, "flow_list": name,
                       }, ensure_ascii=False, sort_keys=True)}
