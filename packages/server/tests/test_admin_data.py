from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from wuwa_story.api.routes.admin_data import (
    ClientAssetDownloadRequest,
    MapBuildRequest,
    SnapshotImportRequest,
    enqueue_client_download,
    enqueue_map_build,
    enqueue_snapshot_import,
    router,
)
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.ingestion.github_snapshots import RemoteSnapshot


def test_data_operations_mutations_require_admin_and_csrf():
    for route in router.routes:
        if "POST" in getattr(route, "methods", set()):
            dependencies = [dependency.call for dependency in route.dependant.dependencies]
            assert require_admin in dependencies
            assert require_csrf in dependencies


def test_snapshot_import_accepts_only_major_minor_versions():
    assert SnapshotImportRequest(version="3.7").version == "3.7"
    with pytest.raises(ValidationError):
        SnapshotImportRequest(version="3.7.0")


def test_client_asset_download_validation():
    req = ClientAssetDownloadRequest(version="3.7.0", tier="hd")
    assert req.version == "3.7.0"
    assert req.tier == "hd"

    # Supports JSON strings (e.g. from clients that stringified the body twice)
    req_from_str = ClientAssetDownloadRequest.model_validate('{"version":"3.7.0","tier":"hd"}')
    assert req_from_str.version == "3.7.0"
    assert req_from_str.tier == "hd"

    req_none = ClientAssetDownloadRequest(tier="sd")
    assert req_none.version is None
    assert req_none.tier == "sd"

    with pytest.raises(ValidationError):
        ClientAssetDownloadRequest(tier="invalid")


def test_map_build_request_validation():
    req = MapBuildRequest(version="3.7.0", tier="hd")
    assert req.version == "3.7.0"
    assert req.download_id is None

    req_with_id = MapBuildRequest(
        version="3.7.0", tier="hd", download_id="a" * 64
    )
    assert req_with_id.download_id == "a" * 64

    with pytest.raises(ValidationError):
        MapBuildRequest(download_id="short")


@pytest.mark.asyncio
async def test_snapshot_import_rejects_when_upstream_has_no_matching_branch(monkeypatch):
    monkeypatch.setattr(
        "wuwa_story.api.routes.admin_data.discover_remote_snapshots",
        lambda *_: (_ for _ in ()).throw(ValueError("no branch")),
    )
    with pytest.raises(HTTPException) as error:
        await enqueue_snapshot_import(SnapshotImportRequest(version="3.7"), AsyncMock())
    assert error.value.status_code == 400
    assert "was not found" in error.value.detail


@pytest.mark.asyncio
async def test_snapshot_import_reports_unavailable_upstream(monkeypatch, caplog):
    monkeypatch.setattr(
        "wuwa_story.api.routes.admin_data.discover_remote_snapshots",
        Mock(side_effect=FileNotFoundError("git executable missing")),
    )
    db = AsyncMock()
    with pytest.raises(HTTPException) as error:
        await enqueue_snapshot_import(SnapshotImportRequest(version="1.0"), db)
    assert error.value.status_code == 400
    assert "retry" in error.value.detail
    assert "git executable missing" in caplog.text
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_import_queues_pinned_patch_without_local_checkout(monkeypatch):
    discover = Mock(return_value=[RemoteSnapshot("1.0", "a" * 40)])
    publish = AsyncMock()
    monkeypatch.setattr("wuwa_story.api.routes.admin_data.discover_remote_snapshots", discover)
    monkeypatch.setattr("wuwa_story.api.routes.admin_data.publish_media_job", publish)
    db = AsyncMock()
    db.scalar.side_effect = [1, None]
    db.add = Mock(side_effect=lambda run: setattr(run, "id", 42))
    result = await enqueue_snapshot_import(SnapshotImportRequest(version="1.0"), db)
    assert result["status"] == "queued"
    assert result["commit"] == "a" * 40
    payload = publish.await_args.args[0]
    assert payload["branch"] == "1.0"
    assert payload["commit"] == "a" * 40
    assert payload["run_id"] == 42
    discover.assert_called_once_with(
        "https://github.com/Arikatsu/WutheringWaves_Data.git", "1.0", "1.0"
    )


@pytest.mark.asyncio
async def test_enqueue_client_download_handles_discovery_failure(monkeypatch):
    monkeypatch.setattr(
        "wuwa_story.api.routes.admin_data.discover_plan",
        lambda *_: (_ for _ in ()).throw(ValueError("Manifest not found")),
    )
    with pytest.raises(HTTPException) as error:
        await enqueue_client_download(ClientAssetDownloadRequest(version="3.7.0"), AsyncMock())
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_enqueue_map_build_rejects_without_installed_client(monkeypatch):
    monkeypatch.setattr(
        "wuwa_story.api.routes.admin_data.inspect_installed_clients",
        lambda *_: [],
    )
    db = AsyncMock()
    db.scalars.return_value = []
    with pytest.raises(HTTPException) as error:
        await enqueue_map_build(MapBuildRequest(version="3.7.0", tier="hd"), db)
    assert error.value.status_code == 400
    assert "download the game client first" in error.value.detail.lower()
