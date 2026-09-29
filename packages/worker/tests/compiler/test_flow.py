import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.flow import compile_flow


class FlowCompilerTests(unittest.TestCase):
    def test_inline_options_sequence_jump_phone_and_unknown_are_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, rows in {
                "BinData/flow/flow.json": [{"Id": "story_1", "States": [1]}],
                "BinData/speaker/speaker.json": [{"Id": 200144}],
                "BinData/cgVedio/videodata.json": [{"CgName": "C1"}],
            }.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows))
            actions = [
                {"Name": "ShowTalk", "Params": {
                    "TalkItems": [
                        {"Id": 7, "Type": "Talk", "WhoId": 200144, "TidTalk": "line.one",
                         "Options": [{"TidTalkOption": "choice.one", "Actions": [
                             {"Name": "JumpTalk", "Params": {"TalkId": 9}}]}]},
                        {"Id": 9, "Type": "PhoneMessage", "TidTalk": "line.two"},
                        {"Id": 10, "Type": "FutureType", "TidTalk": "missing.key"},
                        {"Id": 9, "Type": "Talk", "TidTalk": "line.two"},
                    ], "TalkSequence": [[7, 9]],
                    "SequenceTransitions": {"0": [{"NextSequenceIndex": 0,
                                                   "OptionTextKey": "choice.one"}]}}},
                {"Name": "PlayMovie", "Params": {"VideoName": "C1"}},
                {"Name": "PlayMovie", "Params": {"VideoName": " C1"}},
            ]
            state = root / "BinData/flowState/flowstate.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps([{"StateKey": "story_1_1", "Id": 1,
                                          "Actions": json.dumps(actions)}]))
            writer = Writer(root / "out", "fixture")
            writer.emit("speaker", {"id": "speaker:200144", "source": {}, "raw": {}})
            writer.emit("cutscene", {"id": "cutscene:C1", "source": {}, "raw": {}})
            compile_flow(root, writer, {key: {"content": key, "source": "fixture"}
                                        for key in ("line.one", "line.two", "choice.one")},
                         {"ShowTalk", "PlayMovie"})
            writer.finish()
            edges = [json.loads(line) for line in (root / "out/graphs/global.jsonl").read_text().splitlines()]
            codes = [row["code"] for row in writer.diagnostics]
            self.assertIn("phone_message:story_1_1:0:1", writer.ids)
            self.assertIn("choice:story_1_1:0:0:0", writer.ids)
            self.assertIn("action:story_1_1:1", writer.ids)  # missing ActionId is legal
            self.assertIn("unknown_talk_item_type", codes)
            self.assertIn("duplicate_local_talk_id", codes)
            self.assertIn("unresolved_choice_target", codes)
            self.assertIn("unresolved_localization", codes)
            self.assertTrue(any(e["type"] == "plays_cutscene" and e["to"] == "cutscene:C1" for e in edges))
            self.assertTrue(any(e["type"] == "plays_cutscene" and
                                e.get("resolution") == "unique_whitespace_normalization" and
                                e.get("raw_value") == " C1" for e in edges))
            self.assertTrue(any(e["type"] == "presents_choice" for e in edges))


if __name__ == "__main__":
    unittest.main()
