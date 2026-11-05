from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.references import compile_reference_tables


class ReferenceTableTests(unittest.TestCase):
    def test_dungeon_playflow_condition_compiles_quest_ownership_not_just_source_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = "剧情_3_1_拉海洛主线_上半_3_28_1"
            tables = {
                "BinData/QuestNodeData/questnodedata.json": [{"Key": "114000031_231", "Data": {
                    "Condition": {"Type": "FinishDungeon", "Condition": {"Type": "DungeonId", "DungeonId": 1555}},
                }}],
                "BinData/instance_dungeon/instancedungeon.json": [{"Id": 1555, "RelatedQuestId": 0}],
                "BinData/LevelPlayData/levelplaydata.json": [{"LevelPlayId": 114000004, "Data": {
                    "Id": 114000004, "LevelId": 1555, "Type": "Dungeon",
                }}],
                "BinData/LevelPlayNodeData/levelplaynodedata.json": [{"Key": "114000004_66", "Data": {
                    "Id": 66, "Condition": {"Type": "PlayFlow", "Flow": {
                        "FlowListName": "剧情_3_1_拉海洛主线_上半_3", "FlowId": 28, "StateId": 1,
                    }},
                }}],
            }
            for relative, rows in tables.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows), encoding="utf-8")
            writer = Writer(root / "out", "fixture")
            for kind, identity in (("quest", "quest:114000031"),
                                   ("quest_node", "quest_node:114000031:231"),
                                   ("flow", "flow:剧情_3_1_拉海洛主线_上半_3_28"),
                                   ("flow_state", f"flow_state:{state}")):
                writer.emit(kind, {"id": identity, "source": {}, "raw": {}})
            counts = compile_reference_tables(root, writer)
            writer.finish()
            edges = [json.loads(line) for line in (
                root / "out/graphs/global.jsonl").read_text(encoding="utf-8").splitlines()]
            ownership = [edge for edge in edges if edge["type"]
                         == "references_dungeon_flow_state"]
            self.assertEqual(counts["dungeon_context_links"], 1)
            self.assertEqual(len(ownership), 1)
            self.assertEqual((ownership[0]["from"], ownership[0]["to"]),
                             ("quest_node:114000031:231", f"flow_state:{state}"))
            self.assertEqual(ownership[0]["relation"], "exact_join")
            self.assertEqual(ownership[0]["source"],
                             "BinData/QuestNodeData/questnodedata.json")
            self.assertEqual(json.loads(ownership[0]["basis"])["flow_source"]["path"],
                             "$[0].Data.Condition.Flow")

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
            writer.emit("flow", {"id": "flow:Story_4",
                        "source": {}, "raw": {}})
            writer.emit("flow_state", {
                        "id": "flow_state:Story_4_2", "source": {}, "raw": {}})
            counts = compile_reference_tables(root, writer)
            writer.finish()
            edges = [json.loads(line) for line in (
                root / "out/graphs/global.jsonl").read_text().splitlines()]
            flow_edges = [edge for edge in edges if edge["type"]
                          == "references_flow_state"]
            self.assertEqual(len(flow_edges), 3)
            self.assertEqual({edge["basis"] for edge in flow_edges}, {
                "FlowListName + FlowId + StateId in one source object",
                "PlotName + FlowId + StateId in one source object"})
            self.assertTrue(
                all(edge["raw_path"].startswith("$[0]") for edge in flow_edges))
            condition = next(
                edge for edge in edges if edge["type"] == "condition_references_quest")
            self.assertEqual(condition["relation"], "runtime_condition")
            self.assertEqual(condition["raw_path"],
                             "$[0].Condition.Conditions[0].QuestId")
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
            self.assertTrue(
                any(node.startswith("source_reference:InteractData") for node in writer.ids))


if __name__ == "__main__":
    unittest.main()
