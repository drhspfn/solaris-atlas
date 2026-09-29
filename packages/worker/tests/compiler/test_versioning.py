import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.cli import _diff, _schema_diagnostics
from wuwa_story_worker.compiler.core import Writer


class VersioningTests(unittest.TestCase):
    def test_new_schema_fails_strict_drift_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            writer = Writer(Path(temporary), "fixture")
            current = {"unsupported_candidate_schemas": {"BinData/new/story.json": "abc"},
                       "supported_schemas": {}, "action_names": ["NewAction"],
                       "talk_item_types": ["FutureTalk"]}
            baseline = {"unsupported_candidate_schemas": {}, "supported_schemas": {},
                        "action_names": [], "talk_item_types": []}
            self.assertEqual(_schema_diagnostics(current, baseline, writer), 3)
            self.assertTrue(all(row["code"] == "schema_drift" for row in writer.diagnostics))

    def test_diff_uses_raw_identity_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version, content in (("v1", "old"), ("v2", "new")):
                path = root / version / "entities/quest.jsonl"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"id": "quest:1", "source": {"raw_path": "$[0]"},
                                            "raw": {"TidName": "quest.name"},
                                            "localization": {"key": "quest.name", "en": content}}) + "\n")
                graph = root / version / "graphs/global.jsonl"
                graph.parent.mkdir(parents=True)
                graph.write_text("" if version == "v1" else '{"from":"quest:1","to":"item:2"}\n')
            result = _diff(root / "v1", root / "v2", root / "diff.json")
            self.assertEqual(result["categories"]["added"], [])
            self.assertEqual(result["categories"]["changed"], ["quest:1"])
            self.assertEqual(result["categories"]["localization_changed"], ["quest:1"])
            self.assertEqual(result["categories"]["graph_changed"], ["graphs/global.jsonl"])

    def test_version_and_commit_metadata_alone_do_not_change_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version in ("3.6.0", "3.7.0"):
                entity = root / version / "entities/quest.jsonl"
                entity.parent.mkdir(parents=True)
                entity.write_text(json.dumps({"id": "quest:1", "kind": "quest",
                                              "version": version, "source_commit": version,
                                              "source_repository": "Arikatsu/WutheringWaves_Data",
                                              "source": {"file": "BinData/QuestData/questdata.json",
                                                         "raw_path": "$[0]"},
                                              "raw": {"QuestId": 1}}) + "\n")
                edge = root / version / "graphs/global.jsonl"
                edge.parent.mkdir(parents=True)
                edge.write_text(json.dumps({"from": "quest:1", "to": "quest:1",
                                            "type": "self", "basis": "fixture",
                                            "source": "fixture", "raw_path": "$[0]",
                                            "version": version, "source_commit": version}) + "\n")
            result = _diff(root / "3.6.0", root / "3.7.0", root / "diff.json")
            self.assertEqual(result["counts"]["changed"], 0)
            self.assertEqual(result["counts"]["graph_changed"], 0)


if __name__ == "__main__":
    unittest.main()
