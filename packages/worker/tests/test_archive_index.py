import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import archive_index, entity_media
from wuwa_story_worker.client_assets import workspace_lock


@pytest.mark.parametrize("path", ["../outside.wem", "/absolute.wem", "Audio/../../outside.wem"])
def test_archive_filter_rejects_paths_outside_game_packages(path):
    with pytest.raises(ValueError, match="Unsupported"):
        archive_index.voice_filter_batches([path])


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


@pytest.mark.asyncio
async def test_ensure_archive_index_serializes_concurrent_creation(tmp_path, monkeypatch):
    archive, keys = tmp_path / "archive", tmp_path / "keys"
    (archive / "game").mkdir(parents=True)
    keys.mkdir()
    (keys / "keys.txt").write_text("fixture", encoding="utf-8")
    fmodel = tmp_path / "fmodel"
    entered, release = asyncio.Event(), asyncio.Event()
    calls, builds = [], []
    actual_index = archive_index.index_voice_paths

    async def listing(args, log, timeout):
        calls.append(args)
        assert args == [fmodel.resolve(), (archive / "game").resolve(),
                        "@" + str((keys / "keys.txt").resolve()), "--list"]
        assert timeout == 7200
        with pytest.raises(BlockingIOError), workspace_lock(archive / "character-index-lock"):
            pytest.fail("Archive listing must hold the shared creation lock")
        entered.set()
        await release.wait()
        log.write_text("[File] Client/Content/Aki/Movies/Storyboard/R0012.uasset\n"
                       "[File] Client/Content/Aki/Movies/Storyboard/R0012.uexp\n"
                       "[Done] Listed 2 files\n", encoding="utf-8")

    def index(log):
        builds.append(log)
        return actual_index(log)

    monkeypatch.setattr(entity_media, "tool", listing)
    monkeypatch.setattr(archive_index, "index_voice_paths", index)
    tasks = [asyncio.create_task(archive_index.ensure_archive_index(archive, fmodel, keys)) for _ in range(3)]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert len(calls) == 1
        release.set()
        indexes = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert indexes == [archive / "archive-index.sqlite"] * 3
        assert len(builds) == len(calls) == 1
        assert await archive_index.ensure_archive_index(archive, fmodel, keys) == indexes[0]
        assert len(builds) == len(calls) == 1
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_package_lookup_requires_exact_full_stem_and_export_pair(tmp_path):
    stems = ["Client/Content/Aki/Movies/Storyboard/R0012",
             "Client/Content/Aki/Movies/Storyboard/R00120",
             "Client/Content/Aki/Movies/Elsewhere/R0013",
             "Client/Content/Aki/WwiseAudio/Events/PLAY_SCENE"]
    paths = [stem + suffix for stem in stems for suffix in (".uasset", ".uexp")]
    paths += ["Client/Content/Aki/Movies/Storyboard/R0014.uasset",
              "Client/Content/Aki/Movies/Storyboard/R0015.uexp",
              "Client/Content/Aki/Movies/Storyboard/R0016.uasset",
              "Client/Content/Aki/Movies/Elsewhere/R0016.uexp"]
    log = tmp_path / "archive-index.log"
    log.write_text("".join(f"[File] {path}\n" for path in paths) + f"[Done] Listed {len(paths)} files\n",
                   encoding="utf-8")
    index = archive_index.index_voice_paths(log)
    expected = [f"Aki/Movies/Storyboard/R{number:04}" for number in range(12, 17)]
    expected += ["Aki/Movies/Storyboard/R001", "Aki/Movies/Storyboard/R0012_extra",
                 "aki/wwiseaudio/events/play_scene"]
    assert archive_index.indexed_package_paths(index, iter(expected)) == {
        "Aki/Movies/Storyboard/R0012": stems[0] + ".uasset",
        "aki/wwiseaudio/events/play_scene": stems[3] + ".uasset",
    }


@pytest.mark.parametrize("path", ["/Game/Aki/Scene", "Aki/../Scene", "Aki/Scene.uasset", "Aki//Scene"])
def test_package_lookup_rejects_non_authored_stems(tmp_path, path):
    with pytest.raises(ValueError, match="authored extensionless"):
        archive_index.indexed_package_paths(tmp_path / "unused.sqlite", [path])


@pytest.mark.asyncio
async def test_patch_workspace_reuses_retained_client_index(tmp_path, monkeypatch):
    client = tmp_path / "assets" / "client"
    (client / "game").mkdir(parents=True)
    workspace = tmp_path / "patch-workspaces" / "42"
    workspace.mkdir(parents=True)
    try:
        (workspace / "game").symlink_to(client / "game", target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks requires OS support/permission")
    (workspace / "workspace.json").write_text(json.dumps({"source": str(client)}), encoding="utf-8")
    log = client / "archive-index.log"
    log.write_text("[File] Client/Content/Aki/UI/Image/Actual.uasset\n"
                   "[File] Client/Content/Aki/UI/Image/Actual.uexp\n[Done] Listed 2 files\n", encoding="utf-8")
    index = archive_index.index_voice_paths(log)
    tool = AsyncMock(side_effect=AssertionError("must reuse retained index"))
    monkeypatch.setattr(entity_media, "tool", tool)
    assert await archive_index.ensure_archive_index(workspace, tmp_path / "fmodel", workspace) == index
    assert not (workspace / "archive-index.sqlite").exists()
    tool.assert_not_awaited()
