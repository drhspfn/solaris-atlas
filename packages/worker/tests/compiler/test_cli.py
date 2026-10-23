from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from wuwa_story_worker.compiler import cli
from wuwa_story_worker.compiler.cli import main
from wuwa_story_worker.compiler.inventory import REQUIRED_NARRATIVE_TABLES
from wuwa_story_worker.compiler_cache import compiler_fingerprint


def test_manifest_identifies_compiler_even_with_pinned_epoch_and_unchanged_counts(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("Game Version: 1.0.0\n", encoding="utf-8")
    tables = set(REQUIRED_NARRATIVE_TABLES) | {
        "BinData/QuestData/questdata.json", "BinData/QuestNodeData/questnodedata.json",
        "BinData/flow/flow.json", "BinData/flowState/flowstate.json",
        "BinData/speaker/speaker.json", "BinData/cgVedio/videodata.json",
        "Textmaps/en/multi_text/MultiText.json", "Textmaps/en/subtitle_text/SubtitleText.json",
    }
    for name in tables:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]", encoding="utf-8")
    (source / "BinData/QuestData/questdata.json").write_text(
        '[{"QuestId":1,"Data":{}}]', encoding="utf-8")
    compiler = tmp_path / "compiler"
    compiler.mkdir()
    code = compiler / "entities.py"
    code.write_text("value = 'old'", encoding="utf-8")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    monkeypatch.setattr(cli, "compiler_fingerprint", lambda: compiler_fingerprint(compiler))
    dist = tmp_path / "dist"
    assert cli.build(source, dist, None, False) == 0
    manifest_path = dist / "1.0.0" / "manifest.json"
    first = manifest_path.read_bytes()
    before = json.loads(first)
    assert before["compiler_source_sha256"] == compiler_fingerprint(compiler)
    code.write_text("value = 'new'", encoding="utf-8")
    assert cli.build(source, dist, None, False) == 0
    second = manifest_path.read_bytes()
    after = json.loads(second)
    assert after["compiler_source_sha256"] == compiler_fingerprint(compiler)
    assert after["compiler_source_sha256"] != before["compiler_source_sha256"]
    assert hashlib.sha256(first).digest() != hashlib.sha256(second).digest()
    assert {k: v for k, v in before.items() if k != "compiler_source_sha256"} == {
        k: v for k, v in after.items() if k != "compiler_source_sha256"}


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
