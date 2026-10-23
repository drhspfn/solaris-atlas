import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from wuwa_story.ingestion.entity_media import MediaRequest

from wuwa_story_worker import entity_media
from wuwa_story_worker.media_provenance import temporary_entity_workspace


def request():
    return MediaRequest(download_id="a" * 64, asset_version="3.7.0", tier="hd",
                        game_version="1.1.0", entities=["character:1"], kinds=["image"])


def client(tmp_path):
    value = request()
    root = tmp_path / "assets" / f"3.7.0-hd-{value.download_id[:16]}"
    (root / "game").mkdir(parents=True)
    (root / "game" / "retained.pak").write_bytes(b"compressed client")
    (root / "plan.json").write_text(json.dumps({"id": value.download_id, "version": "3.7.0", "tier": "hd"}))
    (root / "status.json").write_text(json.dumps({"id": value.download_id, "state": "downloaded"}))
    (root / "keys.txt").write_text("private test keys")
    return root


@pytest.mark.parametrize("fail", [False, True])
def test_standalone_scratch_is_removed_and_archives_survive(tmp_path, fail):
    root = client(tmp_path)
    scratch = None
    try:
        with temporary_entity_workspace(root) as scratch:
            assert (scratch / "game" / "retained.pak").read_bytes() == b"compressed client"
            (scratch / "export.uasset").write_bytes(b"extracted")
            if fail:
                raise RuntimeError("publication failed")
    except RuntimeError:
        assert fail
    assert not scratch.exists()
    assert (root / "game" / "retained.pak").read_bytes() == b"compressed client"
    assert not (root / "export.uasset").exists()


@pytest.mark.asyncio
async def test_cancellation_waits_for_publication_before_cleanup(tmp_path, monkeypatch):
    root = client(tmp_path)
    monkeypatch.setattr(entity_media, "asset_workspace", lambda: tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()
    roots = []

    async def publish(payload, connection, scratch):
        roots.append(scratch)
        started.set()
        await finish.wait()
        assert scratch.exists()
        (scratch / "published").write_text("done")

    monkeypatch.setattr(entity_media, "_process_entity_workspace", publish)
    task = asyncio.create_task(entity_media._process_entity_media({"request": request().model_dump()}, None))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and roots[0].exists()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not roots[0].exists()
    assert (root / "game" / "retained.pak").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [True, False])
async def test_worker_exports_only_missing_and_persists_game_identity(tmp_path, monkeypatch, published):
    root = client(tmp_path)
    value = request()
    target = {"owner": 1, "release_id": 2, "path": "/Game/Icon.Icon", "kind": "image", "language": None}
    payload = {"request": value.model_dump(), "run_id": 7, "targets": [target]}
    run = SimpleNamespace(id=7, status="queued", metadata_json={"payload": payload})
    session = AsyncMock()
    session.get.return_value = run
    session.scalar.return_value = None
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(entity_media, "SessionFactory", factory)
    monkeypatch.setattr(entity_media, "validate_plan", lambda _: None)
    monkeypatch.setattr(entity_media, "missing_media_targets", AsyncMock(return_value=[] if published else [target]))
    monkeypatch.setattr(entity_media, "publish_inventory", AsyncMock(return_value={}))
    storage = AsyncMock()
    monkeypatch.setattr(entity_media, "S3Storage", lambda _: storage)
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=11)
    variant, reference = SimpleNamespace(metadata_json={}), SimpleNamespace()
    service.register_variant.return_value = variant
    service.register_reference.return_value = reference
    monkeypatch.setattr(entity_media, "FileRegistrationService", lambda _: service)
    executable = tmp_path / "tool"
    executable.write_bytes(b"pinned tool version")
    monkeypatch.setattr(entity_media, "tool_path", lambda _: executable)
    raw = root / "exports" / "files" / "Client" / "Content" / "Icon.uasset"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"source package")
    image = root / "decoded.png"
    image.write_bytes(b"image")
    build = AsyncMock(return_value=([(target, image, "image", "image/png", [raw])], []))
    monkeypatch.setattr(entity_media, "build_entity_files", build)
    await entity_media._process_entity_workspace(payload, None, root)
    assert run.status == "completed"
    assert run.raw_output["skipped"] == int(published)
    if published:
        build.assert_not_awaited()
        service.register_file.assert_not_awaited()
    else:
        assert build.await_args.args[1] == [target]
        provenance = reference.metadata_json["game_source"]
        assert provenance["download_id"] == value.download_id
        assert provenance["package_path"] == "/Game/Icon"
        assert provenance["source_files"][0]["package_path"] == "Client/Content/Icon.uasset"
        assert len(provenance["source_files"][0]["sha256"]) == 64
        assert "WUWA_FMODEL_PATH" in provenance["tools"]
        assert variant.metadata_json["game_source"] == provenance
