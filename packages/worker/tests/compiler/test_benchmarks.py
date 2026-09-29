import json
import unittest
from collections import Counter
from pathlib import Path


WORKER_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


class BuiltDatasetSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dist = WORKER_ROOT / "dist/3.6.0"
        if not cls.dist.exists():
            raise unittest.SkipTest("Build dist/3.6.0 to run data snapshot checks")
        cls.snapshot = json.loads((FIXTURE_ROOT / "benchmark_snapshot.json").read_text())
        cls.manifest = json.loads((cls.dist / "manifest.json").read_text())

    def test_source_snapshot_and_no_silent_talk_loss(self):
        self.assertEqual(self.manifest["source_commit"], self.snapshot["source_commit"])
        counts = self.manifest["statistics"]["entity_counts"]
        flow = self.manifest["statistics"]["flow_coverage"]
        self.assertEqual(counts["flow_state"], self.snapshot["flow_states"])
        self.assertEqual(flow["raw_talk_items"], self.snapshot["raw_talk_items"])
        self.assertEqual(flow["raw_talk_items"],
                         counts["talk_item"] + counts["narration"] + counts["phone_message"]
                         + flow["player_choice"] - flow["inline_options"])

    def test_quest_graph_snapshots_preserve_benchmarks(self):
        for quest_id, expected in self.snapshot["quest_graphs"].items():
            with self.subTest(quest_id=quest_id):
                graph = json.loads((self.dist / f"graphs/quests/{quest_id}.json").read_text())
                kinds = Counter(node.split(":", 1)[0] for node in graph["node_ids"])
                self.assertEqual(kinds["scene"], expected["scenes"])
                self.assertEqual(kinds["quest_node"], expected["quest_nodes"])
                self.assertEqual(kinds["choice"] + kinds["player_choice"]
                                 + kinds["transition_choice"], expected["choices"])
                self.assertEqual(kinds["cutscene"], expected["cutscenes"])
                self.assertTrue(all(edge.get("basis") and edge.get("raw_path")
                                    for edge in graph["edges"]))


if __name__ == "__main__":
    unittest.main()
