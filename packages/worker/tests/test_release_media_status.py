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
        assert args[-1] == "Audio"
        log.write_text("[Done] Extracted 1 files")
        (voice_root / "plot-audio/en_vo_test.WEM").write_bytes(b"wem")
    monkeypatch.setattr(entity_media, "tool", extract)
    result, status = await release_media.voice_packages({"asset_version": "3.7.0"})
    assert status == "completed"
    assert result["voice_plan_id"] == "fixture"
    assert (voice_root / "audio-export.json").is_file()
