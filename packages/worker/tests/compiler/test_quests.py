"""Small, schema-shaped fixtures for deterministic quest compilation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wuwa_story_worker.compiler.quests import compile_quests


class QuestCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.paths = {key: root / f"{key}.json" for key in
                      ("quest_data", "quest_nodes", "plot_handbook", "flow", "flow_state")}
        self.rows = {
            "quest_data": [
                {"QuestId": 100, "Data": {"Id": 100, "ProvideType": {
                    "Conditions": [{"Type": "PreQuest", "PreQuest": 99}]}}},
                {"QuestId": 99, "Data": {"Id": 99, "ProvideType": {}}},
            ],
            "quest_nodes": [
                {"Key": "100_1", "Data": {"Type": "Sequence", "Id": 1, "ParentNodeId": 0}},
                {"Key": "100_2", "Data": {"Type": "ChildQuest", "Id": 2, "ParentNodeId": 1,
                    "Condition": {"Type": "PlayFlow", "Flow": {
                        "FlowListName": "Plot", "FlowId": 5, "StateId": 7}}}},
                {"Key": "100_3", "Data": {"Type": "QuestSucceed", "Id": 3, "ParentNodeId": 1}},
                {"Key": "100_4", "Data": {"Type": "ChildQuest", "Id": 4, "ParentNodeId": 1}},
                {"Key": "99_1", "Data": {"Type": "Sequence", "Id": 1, "ParentNodeId": 0}},
            ],
            "plot_handbook": [],
            "flow": [{"Id": "Plot_5", "States": [7]}],
            "flow_state": [{"StateKey": "Plot_5_7", "Id": 7, "Actions": "[]"}],
        }

    def compile(self):
        for key, rows in self.rows.items():
            self.paths[key].write_text(json.dumps(rows), encoding="utf-8")
        records, edges, diagnostics = [], [], []
        counts = compile_quests(
            self.paths,
            lambda kind, record: records.append((kind, record)),
            lambda a, b, kind, basis, file, path, extra=None:
                edges.append((a, b, kind, basis, file, path, extra)),
            lambda code, severity, file, path, detail:
                diagnostics.append((code, severity, file, path, detail)),
        )
        return records, edges, diagnostics, counts

    def test_parent_and_flow_links_preserve_basis_and_path(self):
        records, edges, diagnostics, counts = self.compile()
        self.assertFalse(diagnostics)
        self.assertIn(("quest_node:100:1", "quest_node:100:2", "parent_of",
                       "explicit_parent_node_id"), [e[:4] for e in edges])
        flow_edge = next(e for e in edges if e[2] == "references_flow_state")
        self.assertEqual(flow_edge[:4], ("quest_node:100:2", "flow_state:Plot_5_7",
                                         "references_flow_state", "explicit_flow_triple"))
        self.assertEqual(flow_edge[5], "$[1].Data.Condition.Flow")
        self.assertEqual(counts["flow_references"], 1)
        node = next(r for kind, r in records if kind == "quest_node" and r["id"] == "quest_node:100:2")
        self.assertEqual(node["source"]["row"], 1)
        self.assertEqual(node["raw"]["Data"]["Condition"]["Flow"]["StateId"], 7)

    def test_prerequisite_and_condition_slot_are_explicit(self):
        self.rows["quest_nodes"].insert(1, {"Key": "100_5", "Data": {
            "Type": "ConditionSelector", "Id": 5, "ParentNodeId": 1,
            "Slots": [{"Condition": {"Type": 0, "Conditions": []},
                       "Node": {"Type": "ChildQuest", "Id": 2, "Desc": ""}}]}})
        _, edges, diagnostics, _ = self.compile()
        self.assertFalse(diagnostics)
        self.assertIn(("quest:100", "quest:99", "requires_quest", "explicit_pre_quest"),
                      [e[:4] for e in edges])
        slot = next(e for e in edges if e[2] == "condition_slot")
        self.assertEqual(slot[:4], ("quest_node:100:5", "quest_node:100:2",
                                    "condition_slot", "explicit_selector_slot"))
        self.assertEqual(slot[6]["slot_index"], 0)

    def test_sibling_array_adjacency_is_not_runtime_order(self):
        _, edges, _, _ = self.compile()
        sibling_ids = {"quest_node:100:2", "quest_node:100:3", "quest_node:100:4"}
        self.assertFalse(any(a in sibling_ids and b in sibling_ids for a, b, *_ in edges))
        self.assertFalse(any("next" in edge_type or "order" in edge_type
                             for _, _, edge_type, *_ in edges))

    def test_unresolved_flow_is_diagnostic_and_raw_node_is_retained(self):
        self.rows["flow_state"] = []
        records, edges, diagnostics, counts = self.compile()
        self.assertTrue(any(code == "unresolved_flow_state" for code, *_ in diagnostics))
        self.assertTrue(any(kind == "quest_node" and record["id"] == "quest_node:100:2"
                            for kind, record in records))
        self.assertFalse(any(e[2] == "references_flow_state" for e in edges))
        self.assertEqual(counts["unresolved_flow_state"], 1)


if __name__ == "__main__":
    unittest.main()
