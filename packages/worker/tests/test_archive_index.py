import pytest

from wuwa_story_worker import archive_index


def test_shared_index_resolves_numeric_wems_and_cooked_events_without_rescan(tmp_path, monkeypatch):
    log = tmp_path / "main-index.log"
    paths = [f"Client/Content/Aki/WwiseAudio_Generated/Media/{number}.wem" for number in range(1003)]
    paths += ["Audio/ja/42.WEM", "Client/Content/Aki/WwiseAudio/Events/Scene.uasset", "Audio/en/Scene.bnk"]
    log.write_text("".join(f"[File] {path}\n" for path in paths) + f"[Done] Listed {len(paths)} files\n")
    index = archive_index.index_voice_paths(log)
    monkeypatch.setattr(archive_index, "iter_archive_paths", lambda *_: pytest.fail("reuse the disk index"))
    expected = (f"{number}.WEM" for number in range(1003))
    assert archive_index.indexed_audio_paths(index, expected) == sorted(paths[:1004])
    assert archive_index.indexed_audio_paths(index, {"SCENE.UASSET", "scene.bnk"}) == sorted(paths[1004:])
    assert archive_index.indexed_audio_paths(index, {"missing.wem"}) == []


def test_incomplete_index_rebuild_preserves_previous_complete_database(tmp_path):
    log = tmp_path / "archive-index.log"
    log.write_text("[File] Audio/42.wem\n[Done] Listed 1 files\n")
    index = archive_index.index_voice_paths(log)
    previous = index.read_bytes()
    log.write_text("[File] Audio/99.wem\n[Error] interrupted\n")
    with pytest.raises(ValueError, match="inventory failed"):
        archive_index.index_voice_paths(log)
    assert index.read_bytes() == previous
    assert archive_index.indexed_audio_paths(index, {"42.wem", "99.wem"}) == ["Audio/42.wem"]
    assert not index.with_suffix(".partial.sqlite").exists()
