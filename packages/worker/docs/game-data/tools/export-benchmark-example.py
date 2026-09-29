#!/usr/bin/env python3
"""Export a compact source-backed Denia example from a completed build."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist/3.6.0"
QUEST_ID = 119000000
TEXT_KEY = "Main_LahaiRoi_ZRJDYKX_11_21"


def rows(kind: str):
    with (DIST / "entities" / f"{kind}.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def main() -> None:
    graph = json.loads((DIST / f"graphs/quests/{QUEST_ID}.json").read_text())
    nodes = set(graph["node_ids"])
    dialogue = next(row for row in rows("talk_item")
                    if row["id"] in nodes and (row.get("localization") or {}).get("key") == TEXT_KEY)
    speaker = next(row for row in rows("speaker") if row["id"] == f"speaker:{dialogue['speaker_id']}")
    branch = next(row for row in rows("branch") if row["id"] in nodes)
    relevant_edges = [edge for edge in graph["edges"]
                      if edge["from"] in {dialogue["id"], branch["id"]}
                      or edge["to"] == dialogue["id"]]
    output = {"quest_id": QUEST_ID, "source_commit": dialogue["source_commit"],
              "graph_file": f"dist/3.6.0/graphs/quests/{QUEST_ID}.json",
              "graph_counts": {"nodes": len(nodes), "edges": len(graph["edges"])},
              "dialogue": dialogue, "speaker": speaker, "branch": branch,
              "edges": relevant_edges}
    path = ROOT / "samples/denia/compiler-example.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
