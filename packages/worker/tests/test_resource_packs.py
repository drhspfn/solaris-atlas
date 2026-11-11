import hashlib
import json

import pytest

from wuwa_story_worker import client_assets as assets

CDN = "https://hw-pcdownload-qcloud.aki-game.net/"


@pytest.fixture
def packs(monkeypatch):
    bodies = {
        "common": json.dumps({"resource": [
            {"dest": "Client/Content/Paks/base.pak", "md5": "1" * 32, "size": 4},
            {"dest": "Client/Binaries/game.exe", "md5": "2" * 32, "size": 1},
        ]}).encode(),
        "hd": json.dumps({"resource": [
            {"dest": "Client/Content/HD/quality.pak", "md5": "3" * 32, "size": 5},
        ]}).encode(),
    }
    index = {"cdnList": [{"url": CDN}], "resourcePacks": {
        name: {"version": "3.7.2", "indexFile": f"{name}.json",
               "indexFileMd5": hashlib.md5(body).hexdigest(), "baseUrl": f"{name}/zip/"}
        for name, body in bodies.items()
    }}
    def read(url):
        if url == assets.INDEX_URL:
            return json.dumps(index).encode()
        if url.startswith(assets.KEYS_REPOSITORY):
            return json.dumps([{"sha": "a" * 40, "commit": {"message": "[3.7.2]"}}]).encode()
        return bodies[url.removeprefix(CDN).removesuffix(".json")]
    monkeypatch.setattr(assets, "read_url", read)
    return index, bodies


def test_resource_pack_plan_and_downloader_use_each_base(packs, monkeypatch, tmp_path):
    plan = assets.discover_plan("3.7.2", "hd")
    assert plan["version"] == "3.7.2"
    assert [f["base_path"] for f in plan["files"]] == ["hd/zip", "common/zip"]
    assets.validate_plan(plan)
    original_id = plan["id"]
    plan["run_id"] = "arbitrary"
    assert assets.plan_id(plan) == original_id
    urls = {}
    monkeypatch.setattr(assets, "download_file", lambda item, candidates, target: urls.update({item["path"]: candidates[0]}))
    monkeypatch.setattr(assets.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 10**12})())
    monkeypatch.setattr(assets, "read_url", lambda _: json.dumps({"mainKey": "0x" + "a" * 64, "dynamicKeys": []}).encode())
    assets._download_plan(plan, tmp_path, 1)
    assert urls["Client/Content/Paks/base.pak"] == CDN + "common/zip/Client/Content/Paks/base.pak"
    assert urls["Client/Content/HD/quality.pak"] == CDN + "hd/zip/Client/Content/HD/quality.pak"
    plan["files"][0]["base_path"] = "../escape"
    with pytest.raises(ValueError, match="identity"):
        assets.validate_plan(plan)
    plan["id"] = assets.plan_id(plan)
    with pytest.raises(ValueError, match="Unsafe"):
        assets.validate_plan(plan)


@pytest.mark.parametrize("name", ["common", "hd"])
def test_each_pack_manifest_requires_matching_md5(packs, name):
    index, _ = packs
    index["resourcePacks"][name]["indexFileMd5"] = "0" * 32
    with pytest.raises(RuntimeError, match="checksum"):
        assets.discover_plan("3.7.2")


def test_rejects_version_mismatch_and_untrusted_host(packs):
    index, _ = packs
    index["resourcePacks"]["hd"]["version"] = "3.7.1"
    with pytest.raises(ValueError, match="versions do not match"):
        assets.discover_plan("3.7.2")
    index["resourcePacks"]["hd"]["version"] = "3.7.2"
    index["cdnList"][0]["url"] = "https://example.com/"
    with pytest.raises(RuntimeError, match="official HTTPS CDN"):
        assets.discover_plan("3.7.2")


def test_rejects_duplicate_paths_between_packs(packs):
    index, bodies = packs
    bodies["hd"] = bodies["common"]
    index["resourcePacks"]["hd"]["indexFileMd5"] = hashlib.md5(bodies["hd"]).hexdigest()
    with pytest.raises(ValueError, match="Duplicate"):
        assets.discover_plan("3.7.2")


@pytest.mark.parametrize("change", [None, "status", "missing", "bytes", "size", "md5", "path", "tier", "id"])
def test_old_keys_require_complete_identical_validated_download(packs, tmp_path, monkeypatch, change):
    index, bodies = packs
    contents = {"common": b"base", "hd": b"quality"}
    for name, content in contents.items():
        manifest = json.loads(bodies[name])
        manifest["resource"][0].update(size=len(content), md5=hashlib.md5(content).hexdigest())
        bodies[name] = json.dumps(manifest).encode()
        index["resourcePacks"][name]["indexFileMd5"] = hashlib.md5(bodies[name]).hexdigest()
    previous = assets.discover_plan("3.7.2")
    previous["version"] = "3.7.1"
    previous["keys_commit"] = "b" * 40
    if change in {"size", "md5", "path"}:
        field, value = {"size": ("size", 999), "md5": ("md5", "f" * 32),
                        "path": ("path", "Client/Content/Paks/other.pak")}[change]
        previous["files"][1][field] = value
    if change == "tier":
        previous["tier"] = "sd"
    previous["id"] = assets.plan_id(previous)
    root = tmp_path / "assets" / "previous"
    game = root / "game"
    for name, content in contents.items():
        dest = json.loads(bodies[name])["resource"][0]["dest"]
        path = game / dest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if change == "missing":
        (game / "Client/Content/Paks/base.pak").unlink()
    if change == "bytes":
        (game / "Client/Content/Paks/base.pak").write_bytes(b"evil")
    (root / "plan.json").write_text(json.dumps(previous), encoding="utf-8")
    (root / "status.json").write_text(json.dumps({
        "state": "failed" if change == "status" else "downloaded",
        "id": "bad" if change == "id" else previous["id"],
    }), encoding="utf-8")
    old_read = assets.read_url
    def read(url):
        if url.startswith(assets.KEYS_REPOSITORY):
            return b'[{"sha":"' + b'a' * 40 + b'","commit":{"message":"[3.7.0]"}}]'
        return old_read(url)
    monkeypatch.setattr(assets, "read_url", read)
    if change is None:
        plan = assets.discover_plan("3.7.2", completed_workspace=tmp_path)
        assert plan["keys_commit"] == "b" * 40
        assets.validate_plan(plan)
    else:
        with pytest.raises(ValueError, match="No version-labelled AES"):
            assets.discover_plan("3.7.2", completed_workspace=tmp_path)
