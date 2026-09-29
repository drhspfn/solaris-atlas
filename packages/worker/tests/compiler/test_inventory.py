from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.core import is_ignored_fs_entry
from wuwa_story_worker.compiler.inventory import scan, strict_coverage_findings


class InventoryTests(unittest.TestCase):
    def test_strength_tiers_follow_fields_before_filename_hints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindata = root / "BinData"
            (bindata / "QuestThing").mkdir(parents=True)
            (bindata / "role").mkdir(parents=True)
            (bindata / "Combat").mkdir(parents=True)
            (bindata / "QuestThing" / "record.json").write_text(
                json.dumps([{"QuestId": 123, "Value": 1}]))
            (bindata / "QuestThing" / "._hidden.json").write_text("AppleDouble noise")
            (bindata / "role" / "inventory.json").write_text(
                json.dumps([{"Value": 1}]))
            (bindata / "Combat" / "damage.json").write_text(
                json.dumps([{"Attack": 1}]))
            result = scan(root)
            by_file = {row["file"]: row for row in result["tables"]}
            self.assertNotIn("BinData/QuestThing/._hidden.json", by_file)
            self.assertEqual(by_file["BinData/QuestThing/record.json"]["narrative_tier"], "tier1")
            self.assertEqual(by_file["BinData/role/inventory.json"]["narrative_tier"], "tier3")
            self.assertEqual(by_file["BinData/Combat/damage.json"]["narrative_tier"], "out_of_scope")

    def test_macos_metadata_is_ignored(self):
        self.assertTrue(is_ignored_fs_entry("._dialogue.jsonl"))
        self.assertTrue(is_ignored_fs_entry(".DS_Store"))
        self.assertFalse(is_ignored_fs_entry("dialogue.jsonl"))

    def test_strict_coverage_fails_for_new_unclassified_tier1_and_unknown_fields(self):
        findings = strict_coverage_findings([
            {"file": "BinData/New/new.json", "narrative_tier": "tier1",
             "narrative_classification": "raw_evidence_only",
             "unknown_strong_reference_fields": ["FlowTargetKey"]},
            {"file": "BinData/Old/old.json", "narrative_tier": "tier1",
             "narrative_classification": "unclassified"},
        ], {"BinData/Old/old.json"}, unsupported_talk_count=1)
        self.assertEqual(findings["new_tier1_tables"], ["BinData/New/new.json"])
        self.assertEqual(findings["unclassified_tier1_tables"], ["BinData/Old/old.json"])
        self.assertEqual(findings["unknown_strong_reference_fields"],
                         {"BinData/New/new.json": ["FlowTargetKey"]})
        self.assertEqual(findings["unclassified_talk_diagnostics"], 1)


if __name__ == "__main__":
    unittest.main()
