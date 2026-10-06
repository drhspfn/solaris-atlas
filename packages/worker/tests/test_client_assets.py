import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wuwa_story_worker import asset_export, asset_jobs, client_assets
from wuwa_story_worker.client_assets import (
    cdn_url,
    discover_plan,
    download_file,
    md5,
    plan_id,
    safe_path,
    validate_plan,
    workspace_lock,
)


def _item(body=b"archive"):
    return {"path": "Client/Content/Paks/base.pak", "size": len(body), "md5": hashlib.md5(body).hexdigest()}


def _plan():
    plan = {"schema_version": 1, "job_type": "assets.download", "version": "3.7.0", "tier": "hd",
            "keys_commit": "a" * 40, "cdns": ["https://hw-pcdownload-qcloud.aki-game.net/"],
            "base_path": "client/zip", "files": [_item()]}
    plan["id"] = plan_id(plan)
    return plan


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


@pytest.mark.parametrize("path", ["../file.pak", "/file.pak", "C:/file.pak", "a\\file.pak", "a/../file.pak", "a//file.pak"])
def test_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_rejects_untrusted_cdn():
    with pytest.raises(ValueError, match="official"):
        cdn_url("https://example.com/", "client/file.pak")


def test_normalizes_launcher_hash_with_missing_leading_zero():
    assert md5("a" * 31) == "0" + "a" * 31


def test_plan_identity_prevents_silent_mutation():
    plan = _plan()
    plan["files"][0]["size"] += 1
    with pytest.raises(ValueError, match="identity"):
        validate_plan(plan)


def test_discovery_pins_current_version_and_selects_common_and_hd(monkeypatch):
    body = json.dumps({"resource": [_item(), {**_item(), "dest": "Client/Content/HD/hd.pak"},
                                    {**_item(), "dest": "Client/Binaries/game.exe"}]}).encode()
    # Launcher manifests use dest, job plans use path.
    manifest = json.loads(body)
    manifest["resource"][0]["dest"] = manifest["resource"][0].pop("path")
    body = json.dumps(manifest).encode()
    index = {"default": {"config": {"version": "3.7.0", "indexFile": "manifest.json",
                                     "indexFileMd5": hashlib.md5(body).hexdigest(), "baseUrl": "zip/"},
                         "cdnList": [{"url": "https://hw-pcdownload-qcloud.aki-game.net/"}]}}
    def read(url):
        if url == client_assets.INDEX_URL:
            return json.dumps(index).encode()
        if "api.github.com" in url:
            return json.dumps([{"sha": "a" * 40, "commit": {"message": "[3.7.0] Keys update"}}]).encode()
        return body
    monkeypatch.setattr(client_assets, "read_url", read)
    plan = discover_plan("3.7", "hd")
    assert len(plan["files"]) == 2
    assert plan["version"] == "3.7.0"
    assert plan["keys_commit"] == "a" * 40
    with pytest.raises(ValueError, match="not sd"):
        discover_plan("3.7", "sd")
    with pytest.raises(ValueError, match="old patches"):
        discover_plan("1.0", "hd")


def test_download_resumes_and_verifies_md5(tmp_path, monkeypatch):
    body = b"some archive bytes"
    target = tmp_path / "base.pak"
    target.with_name("base.pak.part").write_bytes(body[:5])
    def request(req, timeout):
        assert req.headers["Range"] == "bytes=5-"
        return Response(body[5:], 206, {"Content-Range": f"bytes 5-{len(body)-1}/{len(body)}"})
    monkeypatch.setattr(client_assets, "urlopen", request)
    download_file(_item(body), ["https://cdn/file"], target)
    assert target.read_bytes() == body
    assert not target.with_name("base.pak.part").exists()


def test_download_restarts_if_cdn_ignores_range(tmp_path, monkeypatch):
    body = b"some archive bytes"
    target = tmp_path / "base.pak"
    target.with_name("base.pak.part").write_bytes(body[:5])
    monkeypatch.setattr(client_assets, "urlopen", lambda *a, **k: Response(body))
    download_file(_item(body), ["https://cdn/file"], target)
    assert target.read_bytes() == body


def test_redelivery_skips_verified_files(tmp_path, monkeypatch):
    target = tmp_path / "base.pak"
    target.write_bytes(b"archive")
    monkeypatch.setattr(client_assets, "urlopen", lambda *a, **k: pytest.fail("should not download"))
    download_file(_item(), ["https://cdn/file"], target)


def test_bad_checksum_never_replaces_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "base.pak"
    target.write_bytes(b"old")
    monkeypatch.setattr(client_assets, "urlopen", lambda *a, **k: Response(b"corrupt"))
    monkeypatch.setattr(client_assets.time, "sleep", lambda *a: None)
    with pytest.raises(RuntimeError, match="All CDN"):
        download_file(_item(), ["https://cdn/file"], target)
    assert target.read_bytes() == b"old"


def test_workspace_lock_is_released_after_failure(tmp_path):
    with pytest.raises(RuntimeError):
        with workspace_lock(tmp_path):
            raise RuntimeError("interrupted")
    with workspace_lock(tmp_path):
        pass


def _completed_download(root: Path):
    plan = _plan()
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "status.json").write_text(json.dumps({"state": "downloaded", "id": plan["id"]}))
    (root / "keys.txt").write_text("key")
    executable = root / "FModelCLI.exe"
    executable.write_bytes(b"tool")
    return executable


@pytest.mark.asyncio
async def test_fmodel_exit_zero_with_failed_exports_is_failure(tmp_path, monkeypatch):
    executable = _completed_download(tmp_path)
    def run(command, stdout, **kwargs):
        stdout.write("[Fail] asset: failed\n[Done] Extracted 0 files (Scanned 5).\n")
    monkeypatch.setattr(asset_export.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="FModelCLI failed"):
        await asset_export.export_assets(tmp_path, executable, "ConfigDB")
    assert not list((tmp_path / "exports").glob("*/manifest.json"))


@pytest.mark.asyncio
async def test_export_upload_publishes_manifest_last(tmp_path, monkeypatch):
    executable = _completed_download(tmp_path)
    def run(command, stdout, **kwargs):
        (Path(command[3]) / "data.json").write_text("{}")
        stdout.write('Pak "base.pak": 1 files (1 encrypted)\n[Done] Extracted 1 files (Scanned 5).\n')
    monkeypatch.setattr(asset_export.subprocess, "run", run)
    uploads = []
    class Storage:
        async def ensure_bucket(self):
            pass
        async def put_file(self, source, key, content_type=None):
            uploads.append(key)
    monkeypatch.setattr(asset_export, "S3Storage", lambda settings: Storage())
    receipt = await asset_export.export_assets(tmp_path, executable, "ConfigDB", True)
    assert receipt.exists()
    assert len(uploads) == 2
    assert uploads[-1].endswith("/manifest.json")
    monkeypatch.setattr(asset_export.subprocess, "run", lambda *a, **k: pytest.fail("export should be reused"))
    await asset_export.export_assets(tmp_path, executable, "ConfigDB")


@pytest.mark.asyncio
async def test_partial_mount_is_not_published(tmp_path, monkeypatch):
    executable = _completed_download(tmp_path)
    def run(command, stdout, **kwargs):
        (Path(command[3]) / "data.json").write_text("{}")
        stdout.write('[Done] Extracted 1 files (Scanned 5).\n')
    monkeypatch.setattr(asset_export.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="did not mount"):
        await asset_export.export_assets(tmp_path, executable, "ConfigDB")


@pytest.mark.asyncio
async def test_download_handoff_is_pinned_and_repeatable(tmp_path, monkeypatch):
    plan = _plan()
    monkeypatch.setenv("WUWA_ASSET_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WUWA_ASSET_EXPORT_FILTERS", "ConfigDB,Audio")
    monkeypatch.setattr(asset_jobs, "download_plan", lambda *a: tmp_path)
    published = []
    async def publish(key, payload, message_id):
        published.append((key, payload, message_id))
    monkeypatch.setattr(asset_jobs, "publish_job", publish)
    await asset_jobs.download_client_assets(plan)
    await asset_jobs.download_client_assets(plan)
    assert published[:2] == published[2:]
    assert all(item[0] == "asset_extract" and item[1]["download_id"] == plan["id"] for item in published)
    assert [item[1]["filter"] for item in published[:2]] == ["ConfigDB", "Audio"]


@pytest.mark.asyncio
async def test_map_handoff_uses_download_identity(tmp_path, monkeypatch):
    plan = _plan()
    monkeypatch.setenv("WUWA_ASSET_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WUWA_ASSET_EXPORT_FILTERS", "ConfigDB")
    monkeypatch.setenv("WUWA_ASSET_BUILD_MAPS", "1")
    monkeypatch.setattr(asset_jobs, "download_plan", lambda *args: tmp_path)
    published = []
    async def publish(queue, payload, message_id):
        published.append((queue, payload, message_id))
    monkeypatch.setattr(asset_jobs, "publish_job", publish)
    await asset_jobs.download_client_assets(plan)
    assert len(published) == 2
    assert published[1][0] == "asset_extract"
    assert published[1][1]["job_type"] == "assets.maps"
    assert published[1][1]["download_id"] == plan["id"]
    assert published[1][2] == plan["id"] + "-maps-v1"


def test_download_preflight_rejects_low_disk_space(tmp_path, monkeypatch):
    monkeypatch.setattr(client_assets.shutil, "disk_usage", lambda *a: SimpleNamespace(free=1))
    monkeypatch.setattr(client_assets, "urlopen", lambda *a, **k: pytest.fail("must not download"))
    with pytest.raises(RuntimeError, match="Insufficient disk"):
        client_assets.download_plan(_plan(), tmp_path)


@pytest.mark.asyncio
async def test_download_client_assets_updates_admin_run(tmp_path, monkeypatch):
    plan = {**_plan(), "run_id": 42}
    monkeypatch.setenv("WUWA_ASSET_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(asset_jobs, "download_plan", lambda *a: tmp_path)
    async def noop(*a, **k):
        pass
    monkeypatch.setattr(asset_jobs, "publish_job", noop)
    updates = []
    async def track(run_id, processor, status, **kwargs):
        updates.append((run_id, processor, status, kwargs))
    monkeypatch.setattr(asset_jobs, "update_admin_run", track)
    await asset_jobs.download_client_assets(plan)
    assert updates == [
        (42, "asset_download", "running", {}),
        (42, "asset_download", "completed", {}),
    ]
