from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import release_media


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
        session.commit.assert_awaited_once()
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
    assert build.await_args.kwargs["exact_textures"] is True


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
