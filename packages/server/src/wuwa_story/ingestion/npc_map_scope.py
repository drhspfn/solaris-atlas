"""Authored LevelPlay map context for flow actor resolution."""
from collections import defaultdict

from wuwa_story.ingestion.dungeon_context import decoded, walk

LEVEL_SOURCE = "BinData/LevelPlayData/levelplaydata.json"
LEVEL_NODE_SOURCE = "BinData/LevelPlayNodeData/levelplaynodedata.json"


def flow_map_context(level_rows, node_rows):
    """Retain every caller map; never choose between conflicting map owners."""
    levels = defaultdict(set)
    for _, row in level_rows:
        data = decoded(row.get("Data"))
        if not isinstance(data, dict):
            continue
        identity, map_id = row.get("LevelPlayId", data.get("Id")), data.get("LevelId")
        if type(identity) is int and type(map_id) is int and map_id > 0:
            if data.get("Id", identity) == identity:
                levels[str(identity)].add(map_id)
    result = defaultdict(set)
    for _, row in node_rows:
        key = row.get("Key")
        if not isinstance(key, str):
            continue
        owner, separator, node = key.partition("_")
        if not separator or not owner.isdecimal() or not node.isdecimal():
            continue
        maps = levels.get(owner)
        if not maps:
            continue
        for _, action in walk(row.get("Data")):
            if action.get("Name") != "PlayFlow":
                continue
            params = decoded(action.get("Params"))
            if not isinstance(params, dict):
                continue
            name, flow, state = (params.get(field) for field in ("FlowListName", "FlowId", "StateId"))
            if isinstance(name, str) and name and type(flow) is int and type(state) is int:
                result[f"{name}_{flow}_{state}"].update(maps)
    return dict(result)
