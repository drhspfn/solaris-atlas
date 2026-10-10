import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker.media_diagnostics import archive_paths, voice_report, write_inventory
from wuwa_story_worker.voice_import import exported_voices


def test_inventory_excludes_secrets_and_marks_export_gaps(tmp_path):
    root = tmp_path / "export"
    root.mkdir()
    (root / "en_vo_X.WEM").write_bytes(b"audio")
    (root / "keys.txt").write_text("private", encoding="utf-8")
    log = tmp_path / "index.log"
    log.write_text("[File] en_vo_X.wem\n[File] ja_vo_X.wem\n[File] ../keys.wem\n"
                   "[File] /absolute/path.wem\n[File] secrets.json\n"
                   "[Done] Listed 2 files (Scanned 2).\n", encoding="utf-8")
    destination = tmp_path / "inventory.jsonl"
    summary = write_inventory(root, log, destination)
    assert summary["indexed_files"] == 2
    assert summary["exported_files"] == 1
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert [row["origin"] for row in rows] == ["archive", "archive", "export"]
    assert rows[1]["path"] == "ja_vo_X.wem"
    assert rows[2]["size_bytes"] == 5
    assert "private" not in destination.read_text(encoding="utf-8")


@pytest.mark.parametrize("content", ["[File] en_vo_X.wem\n", "[Error] bad key\n[Done] Listed 0 files\n"])
def test_incomplete_index_is_not_treated_as_proof_of_absence(tmp_path, content):
    log = tmp_path / "index.log"
    log.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        archive_paths(log)


def test_voice_report_distinguishes_missing_export_and_absent_client_resources(tmp_path):
    root = tmp_path / "plot-audio"
    root.mkdir()
    (root / "EN_VO_Line_1_f.WEM").write_bytes(b"audio")
    (root / "en_vo_Line_2.wem").write_bytes(b"other")
    (tmp_path / "archive-index.log").write_text(
        "[File] EN_VO_Line_1_f.WEM\n[File] ja_vo_Line_1_M.wem\n[Done] Listed 2 files\n", encoding="utf-8")
    config = tmp_path / "audio.db"
    with sqlite3.connect(config) as db:
        db.execute("CREATE TABLE plotaudio (Id TEXT)")
        db.execute("INSERT INTO plotaudio VALUES ('Line_1')")
    grouped = exported_voices(root, {"vo_Line_1", "vo_Removed_1"})
    report = voice_report(root, {"vo_Line_1", "vo_Removed_1"}, grouped, config=config)
    entries = {row["expected"]: row for row in report["entries"]}
    assert entries["en_vo_Line_1.wem"]["status"] == "found"
    assert entries["ja_vo_Line_1.wem"]["status"] == "export_missing"
    assert entries["ko_vo_Line_1.wem"]["status"] == "not_in_archives"
    assert entries["ko_vo_Line_1.wem"]["config_present"] is True
    assert entries["en_vo_Removed_1.wem"]["config_present"] is False
    assert report["found"] == 1 and report["missing"] == 7


def test_neighboring_dialogue_is_only_a_diagnostic_candidate(tmp_path):
    (tmp_path / "en_vo_Line_2.wem").write_bytes(b"other")
    report = voice_report(tmp_path, {"vo_Line_1"}, exported_voices(tmp_path, {"vo_Line_1"}))
    entry = report["entries"][0]
    assert entry["matches"] == []
    assert entry["nearby"] == ["en_vo_Line_2.wem"]
    assert entry["status"] == "not_in_export"


def test_entity_inventory_excludes_other_tasks_merged_audio_workspace(tmp_path):
    (tmp_path / "release-media").mkdir()
    (tmp_path / "release-media/other-task.wem").write_bytes(b"audio")
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports/icon.uasset").write_bytes(b"texture")
    output = tmp_path / "inventory.jsonl"
    summary = write_inventory(tmp_path, None, output, exclude=("release-media",))
    assert summary["exported_files"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["path"] == "exports/icon.uasset"


@pytest.mark.asyncio
async def test_voice_lookup_report_is_saved_before_decoder_failure(tmp_path, monkeypatch):
    from wuwa_story_worker import release_media
    voice_root = tmp_path / "voices/3.7.0-plan/plot-audio"
    voice_root.mkdir(parents=True)
    (voice_root / "en_vo_Line_1.wem").write_bytes(b"audio")
    monkeypatch.setattr(release_media, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path / "client")
    monkeypatch.setattr(release_media, "export_assets", AsyncMock(return_value=tmp_path / "config/receipt.json"))
    saved = AsyncMock()
    monkeypatch.setattr(release_media, "update_admin_run", saved)
    async def fail(*args, **kwargs):
        assert saved.await_count >= 1
        assert saved.await_args.kwargs["raw_output"]["media_report"]["missing"] == 3
        raise ValueError("decoder failed")
    monkeypatch.setattr(release_media, "import_voice_sample", fail)
    with pytest.raises(ValueError, match="decoder failed"):
        await release_media.voices({"run_id": 5, "game_version": "1.0.0", "targets": ["vo_Line_1"]},
                                   {"asset_version": "3.7.0", "voice_plan_id": "plan", "media_report": {"inventory_file_id": 9}})


@pytest.mark.asyncio
async def test_missing_export_is_repaired_from_its_pinned_archive(tmp_path, monkeypatch):
    from wuwa_story_worker import entity_media, release_media
    root = tmp_path / "voices/3.7.0-plan"
    exported = root / "plot-audio"
    exported.mkdir(parents=True)
    (root / "archive-index.log").write_text(
        "[File] Audio/ja_vo_Line_1_M.wem\n[Done] Listed 1 files\n", encoding="utf-8")
    monkeypatch.setattr(release_media, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path / "client")
    monkeypatch.setattr(release_media, "export_assets", AsyncMock(return_value=tmp_path / "config/receipt.json"))
    monkeypatch.setattr(release_media, "update_admin_run", AsyncMock())
    async def repair(args, log, timeout):
        assert args[1] == root / "game"
        assert args[-1] == "ja_vo_Line_1"
        (exported / "ja_vo_Line_1_M.wem").write_bytes(b"audio")
    monkeypatch.setattr(entity_media, "tool", repair)
    importer = AsyncMock(return_value={"tracks": 1, "missing_voices": ["en_vo_Line_1"]})
    monkeypatch.setattr(release_media, "import_voice_sample", importer)
    result, status = await release_media.voices(
        {"run_id": 5, "game_version": "1.0.0", "targets": ["vo_Line_1"]},
        {"asset_version": "3.7.0", "voice_plan_id": "plan", "media_report": {"inventory_file_id": 9}})
    assert status == "partial"
    assert result["media_report"]["found"] == 1
    assert ("ja", "male") in importer.await_args.kwargs["grouped"]["vo_Line_1"]
