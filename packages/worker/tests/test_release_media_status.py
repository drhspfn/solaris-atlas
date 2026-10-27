import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import release_media


@pytest.mark.asyncio
async def test_cutscene_preparation_batches_only_available_movie_wrappers(monkeypatch, tmp_path):
    # A preparation-only donor match cannot skip wrappers needed by final-identity leaves.
    monkeypatch.setattr(release_media, "missing_cutscene_identities", AsyncMock(return_value=set()))
    config_files = tmp_path / "config/files"
    config_files.mkdir(parents=True)
    config = config_files / "video.db"
    with sqlite3.connect(config) as db:
        db.execute("CREATE TABLE videodata(CgName TEXT, CgId INTEGER, BinData BLOB)")
        db.executemany("INSERT INTO videodata VALUES (?, ?, ?)", [
            ("Old", 1, b"/Game/Aki/Movies/Old.Old"),
            ("New", 2, b"/Game/Aki/Movies/New.New"),
            ("Second", 3, b"/Game/Aki/Movies/Second.Second"),
        ])
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "video_database", lambda *_args, **_kwargs: config)
    export = AsyncMock(return_value=tmp_path / "config/manifest.json")
    monkeypatch.setattr(release_media, "export_assets", export)
    monkeypatch.setattr(release_media, "ensure_archive_index", AsyncMock(return_value=tmp_path / "index.sqlite"))
    monkeypatch.setattr(release_media, "indexed_package_paths", lambda _index, paths: {
        path: "Client/Content/" + path + ".uasset" for path in paths if not path.endswith("Old")})
    assets = tmp_path / "release-media/files"
    assets.mkdir(parents=True)
    monkeypatch.setattr(release_media, "merge_exports", lambda *_args: assets)
    monkeypatch.setattr(release_media, "publish_inventory", AsyncMock(return_value={}))
    monkeypatch.setattr(release_media, "prepare_base_movies", AsyncMock(return_value={
        "receipts": [], "movies": {}, "missing": [], "errors": {"Bad.uexp": "No movie"}}))
    from wuwa_story_worker import voice_bootstrap
    def discover(_version, _assets, _tier, ids, **_kwargs):
        assert ids == {2, 3}
        return {"id": "video-plan", "files": []}
    monkeypatch.setattr(voice_bootstrap, "discover_video_plan", discover)
    result, status = await release_media.cutscene_assets(
        {"targets": ["Old", "New", "Second"]}, {"workspace_id": 11, "asset_version": "3.7.0", "tier": "hd"})
    assert status == "completed"
    assert result["media_report"]["missing_video_packages"] == ["Aki/Movies/Old"]
    assert result["media_report"]["invalid_movie_sources"] == {"Bad.uexp": "No movie"}
    assert [call.args[2] for call in export.await_args_list] == [
        "ConfigDB", "KuroPublicConfig", "Client/Content/Aki/Movies/New|Client/Content/Aki/Movies/Second"]


@pytest.mark.asyncio
async def test_missing_historical_cutscene_wrapper_is_partial_without_audio_or_publication(monkeypatch, tmp_path):
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "video_database", lambda _: tmp_path / "video.db")
    monkeypatch.setattr(release_media, "plan_cutscene", lambda *_args, **_kwargs: {
        "videos": [{"asset": "asset:ue:/Game/Aki/Movies/Old.Old"}]})
    audio = AsyncMock(side_effect=AssertionError("absent wrapper needs no audio export"))
    publish = AsyncMock(side_effect=AssertionError("must not publish an incomplete variant flow"))
    monkeypatch.setattr(release_media, "extract_cutscene_audio", audio)
    monkeypatch.setattr(release_media, "import_cutscene_recipe", publish)
    result, status = await release_media.cutscene({"targets": ["Old"], "run_id": 90}, {
        "workspace_id": 11, "asset_version": "3.7.0", "voice_plan_id": "voice"})
    assert status == "partial" and result["videos"] == 0
    assert result["missing_assets"] == ["asset:ue:/Game/Aki/Movies/Old.Old"]
    audio.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_authored_movie_is_reported_before_audio_export(monkeypatch, tmp_path):
    wrapper = tmp_path / "release-media/files/Client/Content/Aki/Movies/Wrapper.uexp"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(b"filepath://./Aki/Movies/Actual.mp4\0")
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "video_database", lambda _: tmp_path / "video.db")
    monkeypatch.setattr(release_media, "plan_cutscene", lambda *_args, **_kwargs: {
        "videos": [{"asset": "asset:ue:/Game/Aki/Movies/Wrapper.Wrapper"}]})
    audio = AsyncMock(side_effect=AssertionError("unavailable movie needs no audio extraction"))
    monkeypatch.setattr(release_media, "extract_cutscene_audio", audio)
    result, status = await release_media.cutscene({"targets": ["Scene"], "run_id": 90}, {
        "asset_version": "3.7.0", "voice_plan_id": "voice"})
    assert status == "partial" and result["missing_assets"] == ["Client/Content/Aki/Movies/Actual.mp4"]
    audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_cutscene_audio_finds_base_music_and_init_outside_localized_voice_archives(monkeypatch, tmp_path):
    from wuwa_story_worker import wwise_banks
    voice = tmp_path / "voice"
    voice.mkdir()
    log = voice / "archive-index.log"
    log.write_text("[File] Audio/en/Scene.bnk\n[File] Audio/en/43.wem\n[Done] Listed 2 files\n")
    release_media.index_voice_paths(log)
    main_log = tmp_path / "archive-index.log"
    main_log.write_text("[File] Client/Content/Aki/WwiseAudio_Generated/Media/44.wem\n"
                        "[File] Client/Content/Aki/WwiseAudio_Generated/Init.bnk\n[Done] Listed 2 files\n")
    main_index = release_media.index_voice_paths(main_log)
    config = tmp_path / "video.db"
    with sqlite3.connect(config) as db:
        db.execute("CREATE TABLE videosound(CgName TEXT, BinData BLOB)")
        db.execute("INSERT INTO videosound VALUES (?, ?)", ("Scene", b"/Game/Aki/Audio/Scene.Scene"))
    monkeypatch.setattr(release_media, "package_root", lambda *_args, **_kwargs: voice)
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "ensure_archive_index", AsyncMock(return_value=main_index))
    monkeypatch.setattr(wwise_banks, "bank_media_ids", lambda _: [43, 44])

    async def voice_export(parent, paths, destination):
        for path in paths:
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"source")

    async def base_export(root, fmodel, paths):
        files = tmp_path / "base-export/files"
        for path in paths.split("|"):
            target = files / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"source")
        return files.parent / "manifest.json"

    monkeypatch.setattr(release_media, "extract_voice_paths", voice_export)
    export = AsyncMock(side_effect=base_export)
    monkeypatch.setattr(release_media, "export_assets", export)
    destination = await release_media.extract_cutscene_audio({"voice_plan_id": "voice"}, config, "Scene")
    assert (destination / "Audio/en/43.wem").is_file()
    assert (destination / "Client/Content/Aki/WwiseAudio_Generated/Media/44.wem").is_file()
    assert (destination / "Client/Content/Aki/WwiseAudio_Generated/Init.bnk").is_file()
    assert export.await_count == 1


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
    parent = SimpleNamespace(id=80, status="partial", raw_output={"asset_version": "3.7.0", "workspace_id": 70},
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
@pytest.mark.parametrize("kind,artwork,notices", [
    ("voice_packages", False, False), ("images", False, False), ("cutscene", False, False),
    ("event_catalog", True, False), ("event_images", True, False),
    ("event_resources", True, False), ("event_notices", False, True)])
async def test_queued_legacy_dependency_renews_scoped_pipeline_before_export(
        monkeypatch, kind, artwork, notices):
    from contextlib import asynccontextmanager

    payload = {"schema_version": 1, "job_type": "media.release", "run_id": 90,
               "parent_id": 80, "release_id": 9, "kind": kind}
    run = SimpleNamespace(id=90, status="running", raw_output={"entity_run_id": 50},
                          metadata_json={"payload": payload})
    parent = SimpleNamespace(id=80, status="completed", raw_output={"asset_version": "3.7.0"},
                              metadata_json={"request": {"release_id": 9, "kind": "prepare"}})
    session, connection = AsyncMock(), AsyncMock()
    session.get.side_effect = [run, parent]

    @asynccontextmanager
    async def connect():
        yield connection

    @asynccontextmanager
    async def factory(**kwargs):
        yield session

    @asynccontextmanager
    async def lock(*args):
        yield

    async def renew(*args, **kwargs):
        assert args == (session, 9)
        assert kwargs == {"event_artwork_only": artwork, "notices_only": notices}
        assert run.raw_output is None
        return {"queued": 3}

    monkeypatch.setattr(release_media, "engine", SimpleNamespace(connect=connect))
    monkeypatch.setattr(release_media, "SessionFactory", factory)
    monkeypatch.setattr(release_media, "job_lock", lock)
    renewal = AsyncMock(side_effect=renew)
    monkeypatch.setattr(release_media, "enqueue_release_media", renewal)
    update = AsyncMock()
    monkeypatch.setattr(release_media, "update_admin_run", update)
    monkeypatch.setattr(release_media, "client_root", lambda _: pytest.fail("must not touch retained roots"))
    for name in ("image_batch", "event_images", "event_catalog", "event_resources", "voice_packages", "cutscene", "voices"):
        monkeypatch.setattr(release_media, name, AsyncMock(side_effect=AssertionError("must renew before export")))
    with pytest.raises(release_media.JobDeferred, match="Legacy media dependency renewed"):
        await release_media.process_release_media(payload)
    renewal.assert_awaited_once()
    assert update.await_args.args == (90, "release_media", "waiting_dependency")
    connection.rollback.assert_awaited_once()

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
    paths = [f"Audio/en_vo_{number}.wem" for number in range(400)]
    calls = []

    async def extract(args, log, timeout):
        batch = args[-1].split("|")
        assert 0 < len(batch) <= 256 and set(batch).issubset(paths)
        assert len(args[-1].encode("ascii")) <= 24 * 1024
        calls.append(batch)
        for relative in batch:
            path = args[3] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"wem")
        log.write_text("[Done] Extracted files")

    monkeypatch.setattr(entity_media, "tool", extract)
    await release_media.extract_voice_paths(parent, paths, voice_root / "batch/files")
    assert [len(batch) for batch in calls] == [256, 144]
    assert [path for batch in calls for path in batch] == sorted(paths)
    await release_media.extract_voice_paths(parent, paths, voice_root / "batch/files")
    assert len(calls) == 2  # Successful exact exports are reusable on retry.


@pytest.mark.parametrize("count,length", [(530, 30), (80, 1000)])
def test_voice_filter_batches_are_deterministic_bounded_and_complete(count, length):
    paths = [f"Audio/{number:04d}_" + "a" * length + ".wem" for number in range(count)]
    batches = release_media.voice_filter_batches(reversed(paths))
    assert batches == release_media.voice_filter_batches(paths + paths[:3])
    assert [path for batch in batches for path in batch] == paths
    assert all(len(batch) <= 256 and len("|".join(batch).encode("ascii")) <= 24 * 1024
               for batch in batches)
    for batch, following in zip(batches, batches[1:], strict=False):
        assert len(batch) == 256 or len("|".join([*batch, following[0]]).encode("ascii")) > 24 * 1024


def test_voice_filter_byte_budget_counts_separators_at_exact_boundary():
    first = "a" * 12000
    second = "b" * (24 * 1024 - len(first) - 1)
    assert release_media.voice_filter_batches([second, first]) == [[first, second]]
    assert release_media.voice_filter_batches([second + "b", first]) == [[first], [second + "b"]]
    assert release_media.voice_filter_batches([]) == []


@pytest.mark.parametrize("path", ["Audio/non-ascii_я.wem", "Audio/file.wem|Audio/other.wem", "a" * (24 * 1024 + 1)])
def test_voice_filter_rejects_invalid_or_oversized_paths(path):
    with pytest.raises(ValueError):
        release_media.voice_filter_batches(["Audio/valid.wem", path])


@pytest.mark.asyncio
async def test_cutscene_audio_follows_authored_bank_then_exact_media_id(monkeypatch, tmp_path):
    import sqlite3
    from contextlib import closing

    from wuwa_story_worker import entity_media, wwise_banks
    root = tmp_path / "voice"
    root.mkdir()
    log = root / "archive-index.log"
    log.write_text("[File] Audio/en/SceneEvent.bnk\n[File] Audio/en/42.wem\n"
                   "[File] Audio/en/Unrelated.bnk\n[Done] Listed 3 files\n")
    release_media.index_voice_paths(log)
    config = tmp_path / "video.db"
    with closing(sqlite3.connect(config)) as db, db:
        db.execute("CREATE TABLE videosound (CgName TEXT, BinData BLOB)")
        db.execute("INSERT INTO videosound VALUES (?, ?)",
                   ("Scene", b"/Game/Aki/WwiseAudio/Events/SceneEvent.SceneEvent"))
    monkeypatch.setattr(release_media, "package_root", lambda *_args, **_kwargs: root)
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(wwise_banks, "bank_media_ids", lambda _: [42])
    main_log = tmp_path / "archive-index.log"
    main_log.write_text("[Done] Listed 0 files\n")
    main_index = release_media.index_voice_paths(main_log)
    monkeypatch.setattr(release_media, "ensure_archive_index", AsyncMock(return_value=main_index))
    calls = []

    async def extract(args, log, timeout):
        calls.append(args[-1])
        path = args[3] / args[-1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"selected")
        log.write_text("[Done] Extracted 1 files")

    monkeypatch.setattr(entity_media, "tool", extract)
    result = await release_media.extract_cutscene_audio(
        {"workspace_id": 11, "asset_version": "3.7.0", "voice_plan_id": "a" * 64}, config, "Scene")
    assert calls == ["Audio/en/SceneEvent.bnk", "Audio/en/42.wem"]
    assert (result / "Audio/en/42.wem").is_file()
    assert not (result / "Audio/en/Unrelated.bnk").exists()


@pytest.mark.asyncio
async def test_event_image_publication_persists_source_hashes(monkeypatch, tmp_path):
    import hashlib

    original = "/Game/Aki/UI/Texture"
    raw = tmp_path / "Client/Content/Aki/UI/Texture.uasset"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"source bytes")
    png = tmp_path / "converted.png"
    png.write_bytes(b"png")
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "build_icons", AsyncMock(return_value={
        original: {"path": str(png), "raw_paths": [str(raw)]}}))
    storage = SimpleNamespace(ensure_bucket=AsyncMock())
    monkeypatch.setattr(release_media, "S3Storage", lambda _: storage)
    reference = SimpleNamespace()
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=3)
    service.register_reference.return_value = reference
    monkeypatch.setattr(release_media, "FileRegistrationService", lambda _: service)
    session = AsyncMock()
    session.scalar.return_value = None
    result, status = await release_media.event_images(
        {"release_id": 9, "targets": [original]},
        {"asset_version": "3.7.0", "download_id": "a" * 64}, session)
    assert status == "completed" and result["found"] == 1
    assert reference.metadata_json["game_source"]["package_path"] == original
    assert reference.metadata_json["game_source"]["source_files"] == [{
        "package_path": "Client/Content/Aki/UI/Texture.uasset",
        "sha256": hashlib.sha256(b"source bytes").hexdigest()}]


@pytest.mark.asyncio
@pytest.mark.parametrize("variants,produce,publication_failure", [
    (False, True, False), (True, True, False), (True, False, False), (False, True, True)])
async def test_release_exports_only_missing_voice_path_and_preserves_full_roster(
        monkeypatch, tmp_path, variants, produce, publication_failure):
    from wuwa_story_worker import entity_media

    voice_root = tmp_path / "patch-workspaces/11/voices" / ("3.7.0-" + "a" * 16)
    voice_root.mkdir(parents=True)
    sibling = voice_root / "voice-batches/91/files/other.wav"
    shared = voice_root / "exports/files/shared.wem"
    for path in (sibling, shared):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained")
    monkeypatch.setattr(release_media, "asset_workspace", lambda: tmp_path)
    paths = sorted({f"Audio/{language}_vo_test{suffix}.wem"
                    for language in ("en", "ja", "ko", "zh")
                    for suffix in (("_F", "_M") if variants else ("",))}
                   | {f"Audio/{language}_vo_reused.wem" for language in ("en", "ja", "ko", "zh")})
    missing = "Audio/ja_vo_test_M.wem" if variants else "Audio/ja_vo_test.wem"
    (voice_root / "archive-index.log").write_text(
        "".join(f"[File] {path}\n" for path in paths) + f"[Done] Listed {len(paths)} files\n")
    parent = {"workspace_id": 11, "asset_version": "3.7.0", "voice_plan_id": "a" * 64,
              "media_report": {}, "download_id": "b" * 64, "tier": "hd"}
    monkeypatch.setattr(release_media, "package_root", lambda *_args, **_kwargs: voice_root)
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media, "missing_global_voice_identities",
                        AsyncMock(side_effect=AssertionError("must use full archive roster first")))
    select = AsyncMock(return_value={missing})
    monkeypatch.setattr(release_media, "missing_global_voice_paths", select)
    monkeypatch.setattr(release_media, "export_assets", AsyncMock(return_value=tmp_path / "config/manifest.json"))
    monkeypatch.setattr(release_media, "update_admin_run", AsyncMock())
    importer = AsyncMock(return_value={"tracks": int(produce), "missing_voices": []})
    if publication_failure:
        async def fail(root, *_args, **_kwargs):
            (root / "converted.wav").write_bytes(b"retain failed publication")
            raise RuntimeError("publication failed")
        importer.side_effect = fail
    monkeypatch.setattr(release_media, "import_voice_sample", importer)
    commands = []

    async def extract(args, log, timeout):
        commands.append(args[-1])
        if produce:
            path = args[3] / args[-1]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"wem")
        log.write_text("[Done] Extracted files")

    monkeypatch.setattr(entity_media, "tool", extract)
    if publication_failure:
        with pytest.raises(RuntimeError, match="publication failed"):
            await release_media.voices(
                {"run_id": 90, "game_version": "1.4.0", "targets": ["vo_test", "vo_reused"]}, parent)
        assert (voice_root / "voice-batches/90/files/converted.wav").is_file()
        assert sibling.is_file() and shared.is_file()
        return
    result, status = await release_media.voices(
        {"run_id": 90, "game_version": "1.4.0", "targets": ["vo_test", "vo_reused"]}, parent)
    assert commands == [missing]  # No sibling languages, variants, or fuzzy repair.
    assert select.await_args.args == ({"vo_test", "vo_reused"}, paths)
    kwargs = importer.await_args.kwargs
    assert kwargs["source_context"]["voice_paths"] == paths
    exported = [path for tracks in kwargs["grouped"].values() for path in tracks.values()]
    assert [path.relative_to(importer.await_args.args[0]).as_posix() for path in exported] == ([missing] if produce else [])
    assert result["reused"] == len(paths) - 1
    assert status == ("completed" if produce else "partial")
    assert result["missing_voices"] == ([] if produce else [missing])
    report = result["media_report"]
    assert report["found"] == (8 if produce else 7)
    assert report["missing"] == int(not produce)
    assert sum(entry.get("reused", False) for entry in report["entries"]) >= 7
    assert not (voice_root / "voice-batches/90").exists()
    assert sibling.is_file() and shared.is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [("en", "ja", "ko", "zh"), ("en", "ja", "ko")])
async def test_scoped_voice_roster_reuse_skips_extraction_and_reports_absent_language(
        monkeypatch, languages):
    paths = [f"Audio/{language}_vo_test.wem" for language in languages]
    monkeypatch.setattr(release_media, "requested_voice_paths", AsyncMock(return_value=paths))
    select = AsyncMock(return_value=set())
    monkeypatch.setattr(release_media, "missing_global_voice_paths", select)
    monkeypatch.setattr(release_media, "extract_voice_paths", AsyncMock(side_effect=AssertionError("verified paths")))
    monkeypatch.setattr(release_media, "import_voice_sample", AsyncMock(side_effect=AssertionError("verified paths")))
    result, status = await release_media.voices(
        {"targets": ["vo_test"]}, {"workspace_id": 11})
    assert select.await_args.args == ({"vo_test"}, paths)
    assert select.await_args.kwargs["source_context"]["voice_paths"] == paths
    assert result["reused"] == len(paths)
    assert status == ("completed" if len(languages) == 4 else "partial")
    assert result["missing_voices"] == ([] if len(languages) == 4 else ["zh_vo_test"])


@pytest.mark.asyncio
@pytest.mark.parametrize("publication", ["completed", "partial", "failed"])
async def test_cutscene_publication_cleans_only_owned_conversions(monkeypatch, tmp_path, publication):
    root = tmp_path / "patch-workspaces/11"
    assets = root / "release-media/files"
    shared = assets / "Client/Content/Aki/Movies/Scene.uexp"
    sibling = root / "release-media/91/other.mp4"
    for path in (shared, sibling):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained")
    shared.write_bytes(b"filepath://./Aki/Movies/Scene.mp4\0")
    shared.with_suffix(".mp4").write_bytes(b"retained movie")
    monkeypatch.setattr(release_media, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(release_media, "client_root", lambda _: root)
    monkeypatch.setattr(release_media, "video_database", lambda _: tmp_path / "video.db")
    monkeypatch.setattr(release_media, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(release_media.shutil, "which", lambda _: str(tmp_path / "ffmpeg"))
    monkeypatch.setattr(release_media, "update_admin_run", AsyncMock())

    def recipe(*_args, missing_assets, **_kwargs):
        if publication == "partial":
            missing_assets.append("MissingBank")
        return {"videos": [{"asset": "asset:ue:/Game/Aki/Movies/Scene.Scene", "soundtrack": []}]}

    async def publish(recipe, _movies, _banks, _media, work, *_args, **_kwargs):
        assert recipe.is_file()
        (work / "converted.mp4").write_bytes(b"converted video")
        (work / "converted.wav").write_bytes(b"converted audio")
        if publication == "failed":
            raise RuntimeError("publication failed")
        return {"videos": 1}

    monkeypatch.setattr(release_media, "plan_cutscene", recipe)
    monkeypatch.setattr(release_media, "import_cutscene_recipe", publish)
    payload = {"run_id": 90, "game_version": "1.4.0", "targets": ["Scene"]}
    parent = {"workspace_id": 11, "asset_version": "3.7.0", "download_id": "a" * 64, "tier": "hd"}
    if publication == "failed":
        with pytest.raises(RuntimeError, match="publication failed"):
            await release_media.cutscene(payload, parent)
        assert (root / "release-media/90/converted.mp4").is_file()
        assert (root / "release-media/90/converted.wav").is_file()
    else:
        result, status = await release_media.cutscene(payload, parent)
        assert status == publication
        assert result["videos"] == 1 and "media_report" in result
        assert not (root / "release-media/90").exists()
    assert shared.is_file() and sibling.is_file()

@pytest.mark.asyncio
async def test_invalid_authored_movie_is_partial_without_poisoning_other_cutscenes(monkeypatch, tmp_path):
    wrapper = tmp_path / "release-media/files/Client/Content/Aki/Movies/Wrapper.uexp"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(b"filepath://./Aki/Movies/../Other.mp4\0")
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "video_database", lambda _: tmp_path / "video.db")
    monkeypatch.setattr(release_media, "plan_cutscene", lambda *_args, **_kwargs: {
        "videos": [{"asset": "asset:ue:/Game/Aki/Movies/Wrapper.Wrapper"}]})
    audio = AsyncMock(side_effect=AssertionError("invalid source needs no audio extraction"))
    monkeypatch.setattr(release_media, "extract_cutscene_audio", audio)
    result, status = await release_media.cutscene({"targets": ["Scene"], "run_id": 90}, {
        "asset_version": "3.7.0", "voice_plan_id": "voice"})
    assert status == "partial" and result["videos"] == 0
    assert result["media_report"]["entries"][0]["status"] == "invalid_source"
    audio.assert_not_awaited()
