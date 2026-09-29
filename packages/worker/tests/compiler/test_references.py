from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.references import compile_reference_tables


class ReferenceTableTests(unittest.TestCase):
    def test_exact_flow_triples_and_condition_quest_ids_keep_source_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tables = {
                "BinData/BubbleData/bubbledata.json": [
                    {"ActionGuid": "bubble-a", "Params": {"Flow": {
                        "FlowListName": "Story", "FlowId": 4, "StateId": 2}}},
                ],
                "BinData/InteractData/interactdata.json": [
                    {"Guid": "interact-a", "Type": {"Flow": {
                        "FlowListName": "Story", "FlowId": 4, "StateId": 2}},
                     "Condition": {"Conditions": [{"QuestId": 77}]}},
                ],
                "BinData/PhantomBattle/phantombattledialog.json": [
                    {"Id": 10, "PlotName": "Story", "FlowId": 4, "StateId": 2},
                ],
                "BinData/QuestRefMapBlock/questrefmapblockconfig.json": [
                    {"QuestId": 77, "MapBlockId": [1, 5]},
                ],
                "BinData/instance_dungeon/instancedungeon.json": [
                    {"DungeonId": 9, "RelatedQuestId": 77},
                    {"DungeonId": 10, "RelatedQuestId": 0},
                ],
                "BinData/DownLoad/mapblockinfo.json": [
                    {"BlockId": 1, "MapId": 8, "PakName": "Pack_HL"},
                    {"BlockId": 5, "MapId": 900, "PakName": "Pack_HHA"},
                ],
            }
            for relative, rows in tables.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows))
            writer = Writer(root / "out", "fixture")
            writer.emit("quest", {"id": "quest:77", "source": {}, "raw": {}})
            writer.emit("flow", {"id": "flow:Story_4", "source": {}, "raw": {}})
            writer.emit("flow_state", {"id": "flow_state:Story_4_2", "source": {}, "raw": {}})
            counts = compile_reference_tables(root, writer)
            writer.finish()
            edges = [json.loads(line) for line in (root / "out/graphs/global.jsonl").read_text().splitlines()]
            flow_edges = [edge for edge in edges if edge["type"] == "references_flow_state"]
            self.assertEqual(len(flow_edges), 3)
            self.assertEqual({edge["basis"] for edge in flow_edges}, {
                "FlowListName + FlowId + StateId in one source object",
                "PlotName + FlowId + StateId in one source object"})
            self.assertTrue(all(edge["raw_path"].startswith("$[0]") for edge in flow_edges))
            condition = next(edge for edge in edges if edge["type"] == "condition_references_quest")
            self.assertEqual(condition["relation"], "runtime_condition")
            self.assertEqual(condition["raw_path"], "$[0].Condition.Conditions[0].QuestId")
            self.assertEqual(counts["bubble_action"], 1)
            self.assertEqual(counts["interaction"], 1)
            self.assertEqual(counts["quest_map_block_config"], 1)
            self.assertEqual(counts["map_block"], 2)
            related = next(edge for edge in edges if edge["type"] == "references_quest" and
                           edge["raw_path"] == "$[0].RelatedQuestId")
            self.assertEqual(related["to"], "quest:77")
            self.assertEqual(related["basis"], "QuestId")
            self.assertTrue(any(edge["type"] == "references_map_block" and
                                edge["to"] == "map_block:5" for edge in edges))
            self.assertTrue(any(node.startswith("source_reference:InteractData") for node in writer.ids))


if __name__ == "__main__":
    unittest.main()
