from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.cli import main


class CliValidationTests(unittest.TestCase):
    def test_validator_skips_appledouble_entity_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist_root = Path(temporary)
            out = dist_root / "fixture"
            (out / "entities").mkdir(parents=True)
            (out / "graphs").mkdir()
            (out / "manifest.json").write_text(json.dumps({
                "game_version": "fixture", "schema_inventory_hash": "hash",
            }))
            (out / "diagnostics.json").write_text(json.dumps({"count": 0, "items": []}))
            (out / "entities/quest.jsonl").write_text(json.dumps({"id": "quest:1"}) + "\n")
            (out / "entities/._quest.jsonl").write_text("not valid JSONL\n")
            (out / "graphs/global.jsonl").write_text("")
            self.assertEqual(main(["--dist", str(dist_root), "validate", "--version", "fixture",
                                   "--output-only"]), 0)


if __name__ == "__main__":
    unittest.main()
