"""Focused evidence tests for static media compilation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler.media import compile_media


class MediaCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "BinData"
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.diagnostics: list[tuple] = []

    def write(self, relative: str, rows: list[dict]) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding="utf-8")

    def compile(self) -> None:
        compile_media(
            self.root,
            lambda kind, record: self.nodes.update({record["id"]: {**record, "kind": kind}}),
            lambda source_id, target_id, kind, basis, source, path, extra=None: self.edges.append(
                {"from": source_id, "to": target_id, "type": kind, "basis": basis,
                 "source": source, "path": path, "extra": extra}),
            lambda *args: self.diagnostics.append(args),
            localize=lambda key: {"en": f"localized:{key}"},
        )

    def test_cutscene_variants_and_caption_join_by_cg_name(self) -> None:
        self.write("cgVedio/videodata.json", [
            {"CgId": 10, "CgName": "MOVIE_A", "GirlOrBoy": 0, "BelongBranch": 5,
             "CgFile": "/Game/Movie/A_Female"},
            {"CgId": 11, "CgName": "MOVIE_A", "GirlOrBoy": 1, "BelongBranch": 5,
             "CgFile": "/Game/Movie/A_Male"},
        ])
        self.write("cgVedio/videocaption.json", [
            {"CaptionId": 55, "CgName": "MOVIE_A", "CaptionText": "Caption_Key",
             "ShowMoment": 12, "ShowMomentEn": 13, "Duration": 2, "DurationEn": 3},
        ])
        # CaptionId deliberately equals the caption's ID while CgName differs.
        self.write("cgVedio/videosound.json", [
            {"CaptionId": 55, "CgName": "MOVIE_B", "GirlOrBoy": 2,
             "EventPath": "/Game/Wwise/B"},
        ])
        self.compile()

        variants = [n for n in self.nodes.values() if n["kind"] == "cutscene_variant"]
        self.assertEqual(2, len(variants))
        self.assertEqual({0, 1}, {n["girl_or_boy"] for n in variants})
        self.assertEqual({"/Game/Movie/A_Female", "/Game/Movie/A_Male"},
                         {n["raw"]["CgFile"] for n in variants})
        self.assertIn("cutscene:MOVIE_A", self.nodes)
        self.assertIn("cutscene:MOVIE_B", self.nodes)
        caption = self.nodes["caption:55:0"]
        self.assertEqual({"en": "localized:Caption_Key"}, caption["localized_text"])
        self.assertEqual(13, caption["timing"]["ShowMomentEn"])
        self.assertTrue(any(e["from"] == "cutscene:MOVIE_A" and
                            e["to"] == "caption:55:0" and e["type"] == "uses_caption"
                            for e in self.edges))
        self.assertTrue(any(e["from"] == "cutscene:MOVIE_B" and
                            e["to"] == "audio_event:videosound:0" and
                            e["type"] == "uses_audio_event" for e in self.edges))
        self.assertFalse(any(e["from"] == "audio_event:videosound:0" and
                             e["to"] == "caption:55:0" for e in self.edges))
        self.assertFalse(any(e["from"] == "cutscene:MOVIE_A" and
                             e["to"] == "audio_event:videosound:0" for e in self.edges))

    def test_voice_ref_and_quest_video_package_do_not_infer_cutscene(self) -> None:
        self.write("plot_audio/plotaudio.json", [
            {"Id": "Voice_Key", "FileName": "vo_Voice_Key",
             "ExternalSourceSetting": "subtitle_normal"},
        ])
        self.write("QuestRefVideo/questrefvideoconfig.json", [
            {"QuestId": 123, "PakName": "400_2", "OnlineBranch": "branch_3.2",
             "GirlOrBoy": 0},
        ])
        self.write("cgVedio/videodata.json", [
            {"CgId": 1, "CgName": "UNRELATED_MOVIE", "CgFile": "/Game/Movie/Other"},
        ])
        self.compile()

        self.assertEqual("Voice_Key", self.nodes["voice_ref:Voice_Key"]["plot_audio_id"])
        self.assertTrue(any(e["from"] == "voice_ref:Voice_Key" and
                            e["to"] == "asset:plot_audio_filename:vo_Voice_Key" and
                            e["path"] == "$[0].FileName" for e in self.edges))
        self.assertTrue(any(e["from"] == "quest:123" and
                            e["to"] == "quest_video_package_ref:0" and
                            e["path"] == "$[0].QuestId" for e in self.edges))
        self.assertIn("asset:video_package:branch_3.2:400_2", self.nodes)
        self.assertFalse(any(e["from"] == "quest:123" and
                             e["to"] == "cutscene:UNRELATED_MOVIE" for e in self.edges))

    def test_unique_whitespace_only_cg_name_join_preserves_raw_value(self) -> None:
        self.write("cgVedio/videodata.json", [
            {"CgId": 1, "CgName": "M0341A", "CgFile": "/Game/Movie/M0341A"},
        ])
        self.write("cgVedio/videosound.json", [
            {"CgName": " M0341A", "EventPath": "/Game/Wwise/M0341A"},
        ])
        self.compile()
        edge = next(e for e in self.edges if e["type"] == "uses_audio_event_normalized")
        self.assertEqual(edge["from"], "cutscene:M0341A")
        self.assertEqual(edge["extra"]["raw_value"], " M0341A")
        self.assertEqual(edge["extra"]["normalized_value"], "M0341A")
        self.assertEqual(edge["extra"]["resolution"], "unique_whitespace_normalization")
        audio = self.nodes["audio_event:videosound:0"]
        self.assertEqual(audio["raw_cg_name"], " M0341A")

    def test_video_qte_joins_cutscene_by_exact_cg_name(self) -> None:
        self.write("cgVedio/videodata.json", [
            {"CgId": 3, "CgName": "M0266", "CgFile": "/Game/Movie/M0266"},
        ])
        self.write("cgVedio/videoqte.json", [
            {"Id": 2, "CgName": "M0266", "QteId": 2022001, "ShowMoment": 53},
        ])
        self.compile()
        self.assertIn("video_qte:2:0", self.nodes)
        self.assertTrue(any(edge["from"] == "cutscene:M0266" and
                            edge["to"] == "video_qte:2:0" and
                            edge["basis"] == "CgName = videodata.CgName"
                            for edge in self.edges))


if __name__ == "__main__":
    unittest.main()
