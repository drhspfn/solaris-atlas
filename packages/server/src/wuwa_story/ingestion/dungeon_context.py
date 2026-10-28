"""Exact quest-completion → dungeon → LevelPlay flow context, never playthrough order."""

import json

RELATION = "references_dungeon_flow_state"
QUEST_NODES = "BinData/QuestNodeData/questnodedata.json"
LEVELS = "BinData/LevelPlayData/levelplaydata.json"
LEVEL_NODES = "BinData/LevelPlayNodeData/levelplaynodedata.json"
DUNGEONS = "BinData/instance_dungeon/instancedungeon.json"


def decoded(value):
    if isinstance(value, str) and value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def walk(value, path="$"):
    value = decoded(value)
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def requirements(rows):
    """Only explicit FinishDungeon conditions, not proximity, titles or prerequisites."""
    result = {}
    for index, row in rows:
        key = row.get("Key", "")
        parts = key.split("_")
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            continue
        data = decoded(row.get("Data"))
        if not isinstance(data, dict):
            continue
        for path, value in walk(data.get("Condition"), f"$[{index}].Data.Condition"):
            condition = value.get("Condition")
            if value.get("Type") != "FinishDungeon" or not isinstance(condition, dict):
                continue
            dungeon = condition.get("DungeonId")
            if condition.get("Type") != "DungeonId" or type(dungeon) is not int or dungeon <= 0:
                continue
            result.setdefault(dungeon, []).append({
                "owner": f"quest_node:{parts[0]}:{parts[1]}", "quest_id": int(parts[0]),
                "file": QUEST_NODES, "row": index, "path": path,
            })
    return result


def level_memberships(rows, wanted_dungeons):
    result = {}
    for index, row in rows:
        data = decoded(row.get("Data"))
        if not isinstance(data, dict) or data.get("Type") != "Quest":
            continue
        level_id, dungeon = row.get("LevelPlayId"), data.get("LevelId")
        if type(level_id) is int and data.get("Id") == level_id and dungeon in wanted_dungeons:
            result[level_id] = {"dungeon_id": dungeon, "file": LEVELS, "row": index}
    return result


def dungeon_links(required, memberships, node_rows, valid_states):
    for index, row in node_rows:
        key = row.get("Key", "")
        parts = key.split("_")
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            continue
        membership = memberships.get(int(parts[0]))
        if membership is None:
            continue
        data = decoded(row.get("Data"))
        if not isinstance(data, dict) or data.get("Id") != int(parts[1]):
            continue
        for slot in ("EnterActions", "FinishActions"):
            for path, action in walk(data.get(slot), f"$[{index}].Data.{slot}"):
                params = action.get("Params")
                if action.get("Name") != "PlayFlow" or not isinstance(params, dict):
                    continue
                name, flow, state = (params.get(k) for k in ("FlowListName", "FlowId", "StateId"))
                if not isinstance(name, str) or type(flow) is not int or type(state) is not int:
                    continue
                state_key = f"{name}_{flow}_{state}"
                if state_key not in valid_states:
                    continue
                for requirement in required[membership["dungeon_id"]]:
                    yield {
                        "from": requirement["owner"], "to": f"flow_state:{state_key}",
                        "source": QUEST_NODES, "raw_path": requirement["path"],
                        "basis": json.dumps({
                            "meaning": "Dungeon scenario context; completion requirement is not proof every branch occurred",
                            "quest_condition": requirement, "level_play": membership,
                            "level_play_id": int(parts[0]), "level_play_node": key,
                            "flow_source": {"file": LEVEL_NODES, "row": index, "path": path + ".Params"},
                        }, ensure_ascii=False, sort_keys=True),
                    }
