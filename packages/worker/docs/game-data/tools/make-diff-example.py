#!/usr/bin/env python3
"""Produce a small labeled fixture showing version-diff categories."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
WORKER_SRC = Path(__file__).resolve().parents[3] / "src"
sys.path.insert(0, str(WORKER_SRC))
from wuwa_story_worker.compiler.cli import _diff  # noqa: E402


def write_snapshot(root: Path, version: str, name: str, asset: str, with_choice: bool) -> None:
    base = root / version
    entities = base / "entities"
    entities.mkdir(parents=True)
    rows = [
        {"id": "quest:1", "kind": "quest", "source": {"file": "BinData/QuestData/questdata.json",
         "raw_path": "$[0]"}, "raw": {"QuestId": 1, "TidName": "Quest_1_Name"},
         "localization": {"key": "Quest_1_Name", "en": name}},
        {"id": "asset:ue:/Game/Example", "kind": "asset_reference",
         "source": {"file": "BinData/cgVedio/videodata.json", "raw_path": "$[0]"},
         "raw": {"CgFile": asset}},
    ]
    if with_choice:
        rows.append({"id": "choice:1", "kind": "player_choice",
                     "source": {"file": "BinData/flowState/flowstate.json", "raw_path": "$[0].Actions[0]"},
                     "raw": {"TidTalkOption": "Choice_1"}})
    (entities / "fixture.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    graph = base / "graphs/global.jsonl"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"from":"quest:1","to":"choice:1","type":"presents_choice"}\n' if with_choice else "")
    localization = base / "localization/en.jsonl"
    localization.parent.mkdir(parents=True)
    localization.write_text(json.dumps({"namespace": "multi_text", "key": "Quest_1_Name",
                                        "locale": "en", "content": name}) + "\n")


def main() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_snapshot(root, "fixture-before", "Old title", "/Game/Old", False)
        write_snapshot(root, "fixture-after", "Updated title", "/Game/New", True)
        result = _diff(root / "fixture-before", root / "fixture-after",
                       ROOT / "output/version-diff-example.json")
    print(json.dumps(result["counts"], indent=2))


if __name__ == "__main__":
    main()
