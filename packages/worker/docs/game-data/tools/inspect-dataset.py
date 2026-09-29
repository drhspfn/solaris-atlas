#!/usr/bin/env python3
"""Report a compact inventory of the checked-out Arikatsu data tables."""
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "upstream" / "WutheringWaves_Data"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    root_entries = read(DATA / "BinData" / "flow" / "flow.json")
    states = read(DATA / "BinData" / "flowState" / "flowstate.json")
    handbook = read(DATA / "BinData" / "PlotHandBook" / "plothandbookconfig.json")
    quests = read(DATA / "BinData" / "quest" / "quest.json")
    talk_types = Counter()
    action_names = Counter()
    for state in states:
        for action in json.loads(state.get("Actions") or "[]"):
            action_names[action.get("Name", "<missing>")] += 1
            for talk in action.get("Params", {}).get("TalkItems", []):
                talk_types[talk.get("Type", "<missing>")] += 1
    summary = {
        "branch": "3.6",
        "release": "Game 3.6.0 / Resource 3.6.6 / CL 8499915",
        "files": {
            "flow": {"path": "BinData/flow/flow.json", "rows": len(root_entries)},
            "flowState": {"path": "BinData/flowState/flowstate.json", "rows": len(states)},
            "PlotHandBook": {"path": "BinData/PlotHandBook/plothandbookconfig.json", "rows": len(handbook)},
            "quest": {"path": "BinData/quest/quest.json", "rows": len(quests)},
            "video": {"path": "BinData/cgVedio/videodata.json", "rows": len(read(DATA / "BinData/cgVedio/videodata.json"))},
            "sound": {"path": "BinData/cgVedio/videosound.json", "rows": len(read(DATA / "BinData/cgVedio/videosound.json"))},
            "caption": {"path": "BinData/cgVedio/videocaption.json", "rows": len(read(DATA / "BinData/cgVedio/videocaption.json"))},
        },
        "talk_item_types": dict(talk_types),
        "action_name_counts": dict(action_names.most_common(30)),
    }
    out = ROOT / "output" / "dataset-inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
