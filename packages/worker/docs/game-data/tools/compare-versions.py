#!/usr/bin/env python3
"""Compare game identifiers for one handbook quest across two raw snapshots."""
import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def locate(root: Path, *candidates: str) -> Path:
    for rel in candidates:
        path = root / rel
        if path.exists():
            return path
    raise SystemExit(f"Missing expected file under {root}: {', '.join(candidates)}")


def state_data(root: Path):
    path = locate(root, "BinData/flowState/flowstate.json", "ConfigDB/FlowState.json")
    rows = load(path)
    return path, {row.get("StateKey"): row for row in rows if row.get("StateKey")}


def ids_for_state(row):
    actions = json.loads(row.get("Actions") or "[]")
    result = {"ActionId": set(), "TalkId": set(), "TextId": set(),
              "TextKey": set(), "SpeakerId": set()}
    for action in actions:
        if action.get("ActionId") is not None:
            result["ActionId"].add(str(action["ActionId"]))
        for talk in action.get("Params", {}).get("TalkItems", []):
            if talk.get("Id") is not None:
                result["TalkId"].add(str(talk["Id"]))
            if talk.get("TextId") is not None:
                result["TextId"].add(str(talk["TextId"]))
            if talk.get("TidTalk"):
                result["TextKey"].add(talk["TidTalk"])
            if talk.get("WhoId") is not None:
                result["SpeakerId"].add(str(talk["WhoId"]))
            for option in talk.get("Options", []):
                if option.get("TextId") is not None:
                    result["TextId"].add(str(option["TextId"]))
                if option.get("TidTalkOption"):
                    result["TextKey"].add(option["TidTalkOption"])
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("older", type=Path)
    ap.add_argument("newer", type=Path)
    ap.add_argument("quest_id", type=int)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    new_handbook_path = locate(args.newer, "BinData/PlotHandBook/plothandbookconfig.json")
    handbook = next((x for x in load(new_handbook_path) if x.get("QuestId") == args.quest_id), None)
    if handbook is None:
        raise SystemExit(f"QuestId {args.quest_id} not found in newer PlotHandBook")
    keys = []
    for step in json.loads(handbook.get("Data") or "[]"):
        f = step.get("Flow", {})
        if f.get("FlowListName"):
            keys.append(f"{f['FlowListName']}_{f.get('FlowId', 0)}_{f.get('StateId', 0)}")
    old_path, older = state_data(args.older)
    new_path, newer = state_data(args.newer)
    unique = list(dict.fromkeys(keys))
    stable_keys = [k for k in unique if k in older and k in newer]
    groups = {name: {"older": set(), "newer": set()} for name in
              ("ActionId", "TalkId", "TextId", "TextKey", "SpeakerId")}
    for key in stable_keys:
        before, after = ids_for_state(older[key]), ids_for_state(newer[key])
        for name in groups:
            groups[name]["older"].update(before[name])
            groups[name]["newer"].update(after[name])
    comparison = {}
    for name, group in groups.items():
        before, after = group["older"], group["newer"]
        comparison[name] = {"older_count": len(before), "newer_count": len(after),
                            "stable_count": len(before & after),
                            "removed": sorted(before - after),
                            "added": sorted(after - before)}
    report = {"quest_id": args.quest_id,
              "older_root": str(args.older), "older_flowstate": str(old_path),
              "newer_root": str(args.newer), "newer_flowstate": str(new_path),
              "candidate_state_refs": len(keys), "unique_state_keys": len(unique),
              "matched_state_keys": len(stable_keys),
              "missing_in_older": [k for k in unique if k not in older],
              "missing_in_newer": [k for k in unique if k not in newer],
              "identifiers": comparison}
    out = args.out or Path(f"output/version-comparisons/{args.quest_id}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}: {len(stable_keys)}/{len(unique)} stable state keys")


if __name__ == "__main__":
    main()
