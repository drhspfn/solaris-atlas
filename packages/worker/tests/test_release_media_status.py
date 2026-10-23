import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import release_media


@pytest.fixture(autouse=True)
def isolate_source_lookups(monkeypatch):
    monkeypatch.setattr(release_media, "run_workspace_id", AsyncMock(return_value=None))
    monkeypatch.setattr(release_media, "cleanup_workspace", AsyncMock(return_value=False))
    monkeypatch.setattr(release_media, "source_context", AsyncMock(return_value={"tools": {}}))
    async def missing(_session, targets, **kwargs):
        return targets
    monkeypatch.setattr(release_media, "missing_media_targets", missing)
    async def missing_names(names, **kwargs):
        return names
    monkeypatch.setattr(release_media, "missing_global_voice_identities", missing_names)
    monkeypatch.setattr(release_media, "missing_cutscene_identities", missing_names)


@pytest.mark.asyncio
async def test_prepare_records_running_before_client_discovery(monkeypatch):
    events = []
    async def update(*args, **kwargs):
        events.append((args, kwargs))
    def inspect(*args):
        assert events[0][0] == (4, "release_media", "running")
        assert events[0][1]["raw_output"]["stage"] == "client_discovery"
        raise RuntimeError("discovery fixture")
    monkeypatch.setattr(release_media, "update_admin_run", update)
    monkeypatch.setattr(release_media, "inspect_installed_clients", inspect)
    with pytest.raises(RuntimeError, match="discovery fixture"):
        await release_media.prepare(SimpleNamespace(id=4, metadata_json={}), AsyncMock())


@pytest.mark.asyncio
async def test_failed_child_does_not_automatically_enqueue_forever(monkeypatch):
    child = SimpleNamespace(id=80, status="failed", error="SIGKILL")
    session = AsyncMock()
    session.get.return_value = child
    enqueue = AsyncMock()
    monkeypatch.setattr(release_media, "enqueue_entity_media", enqueue)
    with pytest.raises(RuntimeError, match="SIGKILL"):
        await release_media.image_batch({}, {}, SimpleNamespace(raw_output={"entity_run_id": 80}), session)
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_child_is_waited_for_instead_of_failed():
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(id=80, status="waiting_dependency")
    with pytest.raises(release_media.JobDeferred):
        await release_media.image_batch({}, {}, SimpleNamespace(raw_output={"entity_run_id": 80}), session)


@pytest.mark.asyncio
async def test_event_artwork_reports_unresolved_client_texture(monkeypatch, tmp_path):
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    session = AsyncMock()
    async def extract(*args, **kwargs):
        assert session.commit.await_count == 2
        return {}
    build = AsyncMock(side_effect=extract)
    monkeypatch.setattr(release_media, "build_icons", build)
    report, status = await release_media.event_images(
        {"release_id": 9, "targets": ["Common/Image/BgCg/T_RoleShare_SuoMing"]},
        {"asset_version": "3.7.0", "tier": "hd", "download_id": "a" * 64},
        session,
    )

    assert status == "partial"
    assert report["missing"] == 1
    assert build.await_args.args[3] == [{
        "metadata_json": {
            "icon_source": "/Game/Aki/UI/UIResources/Common/Image/BgCg/T_RoleShare_SuoMing"
        }
    }]


@pytest.mark.asyncio
async def test_event_catalog_updates_presentation_and_keeps_missing_history_visible(monkeypatch, tmp_path):
    config = tmp_path / "export/files/ConfigDB"
    config.mkdir(parents=True)
    (config / "db_activity.db").touch()
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "export_assets", AsyncMock(return_value=config.parent.parent / "manifest.json"))
    record = {"table": "Activity", "title_key": "Activity_111800001_Title",
              "banner_path": "/Game/Aki/UI/UIResources/Common/Image/Event",
              "locales": {"en": {"title": "Blooms for the Shadow", "description": "Actual description"}}}
    monkeypatch.setattr(release_media, "read_event_catalog", lambda _: {"102000009": record})
    event = SimpleNamespace(id=1, source_id="2:102000009", title="Limited-time event",
                            description=None, banner_path=None, source_data={})
    missing = SimpleNamespace(id=2, source_id="2:999", title="Historical title", source_data={})
    occurrence = SimpleNamespace(source_data={}, banner_path=None)
    session = AsyncMock()
    session.scalars.side_effect = [[event, missing], [occurrence]]
    parent = {"asset_version": "3.7.0", "tier": "hd", "download_id": "a" * 64}

    report, status = await release_media.event_catalog(
        {"targets": ["2:102000009@revision", "2:999@revision"]}, parent, session,
    )

    assert status == "completed"
    assert report["found"] == 1 and report["missing"] == 1
    assert report["download_id"] == parent["download_id"]
    assert event.title == "Blooms for the Shadow"
    assert event.description == "Actual description"
    assert occurrence.banner_path == record["banner_path"]
    assert occurrence.source_data["client"]["asset_version"] == "3.7.0"
    assert missing.title == "Historical title"
    assert report["entries"][1]["config_present"] is False


@pytest.mark.asyncio
async def test_event_image_queue_resolves_catalog_ids_and_reports_absent_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    build = AsyncMock(return_value={})
    monkeypatch.setattr(release_media, "build_icons", build)
    session = AsyncMock()
    session.scalars.return_value = [SimpleNamespace(source_id="2:999", banner_path=None)]

    report, status = await release_media.event_images(
        {"release_id": 9, "targets": ["2:999@revision"]},
        {"asset_version": "3.7.0"}, session,
    )

    assert status == "partial" and report["missing_images"] == ["2:999"]
    build.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_export_search_includes_wwise_audio_and_uppercase_extension(tmp_path, monkeypatch):
    from wuwa_story_worker import entity_media, voice_bootstrap, voice_packages
    root = tmp_path / "client"
    receipt = root / "exports/config/manifest.json"
    receipt.parent.mkdir(parents=True)
    voice_root = tmp_path / "voices/3.7.0-plan"
    voice_root.mkdir(parents=True)
    monkeypatch.setattr(release_media, "client_root", lambda _: root)
    monkeypatch.setattr(release_media, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "export_assets", AsyncMock(return_value=receipt))
    monkeypatch.setattr(voice_bootstrap, "discover_client_voices", lambda *_: {"id": "fixture"})
    monkeypatch.setattr(voice_packages, "download_voice_plan", lambda *args, **kwargs: voice_root)
    async def extract(args, log, timeout):
        if args[-1] == "--list":
            log.write_text("[File] en_vo_test.WEM\n[Done] Listed 1 files", encoding="utf-8")
            return
        assert args[-1] == "Audio"
        log.write_text("[Done] Extracted 1 files")
        (voice_root / "plot-audio/en_vo_test.WEM").write_bytes(b"wem")
    monkeypatch.setattr(entity_media, "tool", extract)
    monkeypatch.setattr(release_media, "publish_inventory", AsyncMock(return_value={"inventory_file_id": 9}))
    result, status = await release_media.voice_packages({"asset_version": "3.7.0"})
    assert status == "completed"
    assert result["voice_plan_id"] == "fixture"
    assert (voice_root / "audio-export.json").is_file()

@pytest.mark.asyncio
@pytest.mark.parametrize("parent_kind", ["event_catalog", "event_resources"])
async def test_successful_assets_continue_when_other_event_assets_are_missing(monkeypatch, parent_kind):
    from contextlib import asynccontextmanager
    payload = {"schema_version": 1, "job_type": "media.release", "run_id": 90,
               "parent_id": 80, "release_id": 9, "kind": "event_images"}
    run = SimpleNamespace(id=90, status="queued", metadata_json={"payload": payload})
    parent = SimpleNamespace(id=80, status="partial", raw_output={"asset_version": "3.7.0"},
                              metadata_json={"request": {"release_id": 9, "kind": parent_kind}})
    session = AsyncMock()
    session.get.side_effect = [run, parent]
    connection = AsyncMock()
    @asynccontextmanager
    async def connect():
        yield connection
    @asynccontextmanager
    async def factory(**kwargs):
        yield session
    @asynccontextmanager
    async def lock(*args):
        yield
    monkeypatch.setattr(release_media, "engine", SimpleNamespace(connect=connect))
    monkeypatch.setattr(release_media, "SessionFactory", factory)
    monkeypatch.setattr(release_media, "job_lock", lock)
    update = AsyncMock()
    images = AsyncMock(return_value=({"images": 1}, "completed"))
    monkeypatch.setattr(release_media, "update_admin_run", update)
    monkeypatch.setattr(release_media, "event_images", images)
    await release_media.process_release_media(payload)
    images.assert_awaited_once()
    assert update.await_args.args[2] == "completed"

@pytest.mark.asyncio
async def test_prepare_reuses_completed_client_without_download_disk_reserve(monkeypatch, tmp_path):
    import json
    from unittest.mock import Mock
    plan = {"version": "3.7.0", "tier": "hd", "id": "a" * 64,
            "files": [{"path": "Client/Content/Paks/base.pak", "size": 4}]}
    root = tmp_path / "assets" / ("3.7.0-hd-" + "a" * 16)
    archive = root / "game/Client/Content/Paks/base.pak"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"data")
    (root / "keys.txt").write_text("key")
    (root / "status.json").write_text(json.dumps({"state": "downloaded", "id": plan["id"]}))
    (root / "plan.json").write_text(json.dumps(plan))
    monkeypatch.setattr(release_media, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(release_media, "update_admin_run", AsyncMock())
    download = Mock(side_effect=AssertionError("Already downloaded; no reserve needed"))
    monkeypatch.setattr(release_media, "download_plan", download)
    run = SimpleNamespace(id=7, metadata_json={"client_plan": plan})
    result = await release_media.prepare(run, AsyncMock())
    assert result["stage"] == "client_ready"
    assert result["workspace_id"] == run.id
    workspace = release_media.client_root(result)
    assert workspace != root and (workspace / "game").resolve() == root / "game"
    download.assert_not_called()
    archive.unlink()
    with pytest.raises(AssertionError, match="Already downloaded"):
        await release_media.prepare(run, AsyncMock())

@pytest.mark.asyncio
async def test_new_release_reuses_completed_voice_packages(monkeypatch, tmp_path):
    import json
    folder = tmp_path / 'voices/3.7.0-fixture'
    folder.mkdir(parents=True)
    (folder/'plan.json').write_text(json.dumps({'id':'pinned','version':'3.7.0'}))
    (folder/'status.json').write_text(json.dumps({'id':'pinned','state':'downloaded'}))
    (folder/'audio-export.json').write_text(json.dumps({'plan_id':'pinned','media_report':{'found':123}}))
    monkeypatch.setattr(release_media,'asset_workspace',lambda:tmp_path)
    monkeypatch.setattr(release_media,'export_assets',AsyncMock(side_effect=AssertionError('must reuse')))
    result,status=await release_media.voice_packages({'asset_version':'3.7.0','download_id':'same-client'})
    assert status=='completed' and result['voice_plan_id']=='pinned'
    assert result['media_report']=={'found':123}
    release_media.export_assets.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_voice_export_ignores_global_raw_cache_and_reuses_scoped_export(monkeypatch, tmp_path):
    from wuwa_story_worker import entity_media, voice_bootstrap, voice_packages
    from wuwa_story_worker.media_workspace import package_root

    identity = "a" * 64
    parent = {"workspace_id": 11, "asset_version": "3.7.0", "tier": "hd", "download_id": identity}
    client = tmp_path / "assets" / ("3.7.0-hd-" + identity[:16])
    package = tmp_path / "voices" / ("3.7.0-" + identity[:16])
    for source in (client, package):
        (source / "game").mkdir(parents=True)
        (source / "plan.json").write_text(json.dumps({"version": "3.7.0", "id": identity}))
        (source / "status.json").write_text(json.dumps({"state": "downloaded", "id": identity}))
    (client / "keys.txt").write_text("fixture")
    (package / "audio-export.json").write_text(json.dumps({"plan_id": identity, "media_report": {"global": True}}))
    monkeypatch.setattr(release_media, "asset_workspace", lambda: tmp_path)
    root = release_media.client_root(parent)
    receipt = root / "exports/config/manifest.json"
    receipt.parent.mkdir(parents=True)
    monkeypatch.setattr(release_media, "export_assets", AsyncMock(return_value=receipt))
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(voice_bootstrap, "discover_client_voices", lambda *_: {"id": identity})
    monkeypatch.setattr(voice_packages, "download_voice_plan", lambda *_, **__: package)
    calls = []

    async def extract(args, log, timeout):
        calls.append(args)
        log.write_text("[File] Audio/en_vo_test.WEM\n[Done] Listed 1 files", encoding="utf-8")
        if args[-1] != "--list":
            (args[3] / "en_vo_test.WEM").write_bytes(b"wem")

    monkeypatch.setattr(entity_media, "tool", extract)
    monkeypatch.setattr(release_media, "publish_inventory", AsyncMock(return_value={"scoped": True}))
    result, status = await release_media.voice_packages(parent)
    assert status == "completed" and result["workspace_id"] == 11
    assert result["media_report"] == {"scoped": True}
    scoped = package_root(parent, "voices", identity, base=tmp_path)
    assert (scoped / "archive-index.sqlite").is_file()
    assert not list((scoped / "plot-audio").iterdir())
    assert not (package / "plot-audio").exists()
    assert len(calls) == 1  # Preparation indexes archives without exporting all Audio.
    await release_media.voice_packages(parent)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_image_child_receives_prepare_workspace_id(monkeypatch):
    session = AsyncMock()
    session.get.return_value = None
    child = SimpleNamespace(id=30, status="completed", raw_output={"images": 1})
    enqueue = AsyncMock(return_value=child)
    monkeypatch.setattr(release_media, "enqueue_entity_media", enqueue)
    await release_media.image_batch(
        {"game_version": "3.7", "release_id": 9, "targets": ["character:1"], "kind": "images"},
        {"workspace_id": 11, "asset_version": "3.7.0", "download_id": "a" * 64, "tier": "hd"},
        SimpleNamespace(raw_output={}), session,
    )
    assert enqueue.await_args.args[1].workspace_id == 11


def test_merged_voice_tree_is_scanned_once_per_completed_export(tmp_path, monkeypatch):
    source, destination = tmp_path / "voices/plot-audio", tmp_path / "release/files"
    source.mkdir(parents=True)
    (source / "nested").mkdir()
    (source / "nested/voice.wem").write_bytes(b"raw")
    marker = source.parent / "audio-export.json"
    marker.write_text("{}")
    release_media.merge_exports([source], destination)
    assert (destination / "nested/voice.wem").read_bytes() == b"raw"
    monkeypatch.setattr(Path, "rglob", lambda *_: pytest.fail("completed source must not be rescanned"))
    release_media.merge_exports([source], destination)


def test_video_database_lookup_reuses_bounded_workspace_index(tmp_path, monkeypatch):
    import sqlite3

    database = tmp_path / "config/video.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE videodata (CgId INTEGER)")
        db.execute("CREATE TABLE videosound (CgId INTEGER)")
    assert release_media.video_database(tmp_path) == database
    monkeypatch.setattr(Path, "rglob", lambda *_: pytest.fail("database path is already indexed"))
    assert release_media.video_database(tmp_path) == database


@pytest.mark.asyncio
async def test_verified_event_images_are_reused_without_extraction(monkeypatch, tmp_path):
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "missing_media_targets", AsyncMock(return_value=[]))
    build = AsyncMock(side_effect=AssertionError("durable image should be reused"))
    monkeypatch.setattr(release_media, "build_icons", build)
    result, status = await release_media.event_images(
        {"release_id": 9, "targets": ["/Game/Aki/UI/Texture"]},
        {"asset_version": "3.7.0", "download_id": "a" * 64}, AsyncMock())
    assert status == "completed" and result["found"] == 1
    assert result["entries"][0]["reused"]
    build.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_voices_skip_workspace_and_export(monkeypatch):
    monkeypatch.setattr(release_media, "missing_global_voice_identities", AsyncMock(return_value=set()))
    monkeypatch.setattr(release_media, "package_root", lambda *_args, **_kwargs: pytest.fail("no extraction"))
    result, status = await release_media.voices({"targets": ["vo_test"]}, {})
    assert status == "completed" and result["reused"] == 1


@pytest.mark.asyncio
async def test_verified_cutscenes_skip_asset_preparation_and_leaf_extraction(monkeypatch):
    monkeypatch.setattr(release_media, "missing_cutscene_identities", AsyncMock(return_value=set()))
    monkeypatch.setattr(release_media, "client_root", lambda _: pytest.fail("no extraction"))
    result, status = await release_media.cutscene_assets({"targets": ["Scene"]}, {})
    assert status == "completed" and result["reused"] == 1
    result, status = await release_media.cutscene({"targets": ["Scene"]}, {})
    assert status == "completed" and result["reused"] == 1


def test_voice_archive_index_selects_exact_languages_and_rover_variants(tmp_path):
    log = tmp_path / "archive-index.log"
    log.write_text("\n".join([
        "[File] Audio/en_vo_scene.WEM", "[File] Audio/ja_vo_scene_F.wem",
        "[File] Audio/ja_vo_scene_M.wem", "[File] Audio/en_vo_scene_extra.wem",
        "[File] Audio/en_vo_unrelated.wem", "[Done] Listed 5 files", "",
    ]))
    index = release_media.index_voice_paths(log)
    assert release_media.selected_voice_paths(index, {"vo_scene"}) == [
        "Audio/en_vo_scene.WEM", "Audio/ja_vo_scene_F.wem", "Audio/ja_vo_scene_M.wem"]


@pytest.mark.asyncio
async def test_requested_voice_exports_are_bounded_and_do_not_use_full_audio(monkeypatch, tmp_path):
    from wuwa_story_worker import entity_media
    parent = {"workspace_id": 11, "asset_version": "3.7.0", "voice_plan_id": "a" * 64}
    voice_root = tmp_path / "voice"
    voice_root.mkdir()
    monkeypatch.setattr(release_media, "package_root", lambda *_args, **_kwargs: voice_root)
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    paths = [f"Audio/en_vo_{number}.wem" for number in range(43)]
    calls = []

    async def extract(args, log, timeout):
        batch = args[-1].split("|")
        assert 0 < len(batch) <= 20 and set(batch).issubset(paths)
        calls.append(batch)
        for relative in batch:
            path = args[3] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"wem")
        log.write_text("[Done] Extracted files")

    monkeypatch.setattr(entity_media, "tool", extract)
    await release_media.extract_voice_paths(parent, paths, voice_root / "batch/files")
    assert len(calls) == 3
    await release_media.extract_voice_paths(parent, paths, voice_root / "batch/files")
    assert len(calls) == 3  # Successful exact exports are reusable on retry.
