import copy
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.ingestion import client_assets, client_updates

CDN = "https://hw-pcdownload-qcloud.aki-game.net/"


@pytest.fixture
def source(monkeypatch, tmp_path):
    contents = {"common": b"base", "hd": b"quality"}
    dests = {"common": "Client/Content/Paks/base.pak", "hd": "Client/Content/HD/quality.pak"}
    bodies = {name: json.dumps({"resource": [{"dest": dests[name], "size": len(value),
                                               "md5": hashlib.md5(value).hexdigest()}]}).encode()
              for name, value in contents.items()}
    index = {"cdnList": [{"url": CDN}], "resourcePacks": {
        name: {"version": "3.7.2", "indexFile": name + ".json", "baseUrl": name + "/zip/",
               "indexFileMd5": hashlib.md5(body).hexdigest()} for name, body in bodies.items()}}
    commits = [{"sha": "a" * 40, "commit": {"message": "[3.7.2]"}}]
    def read(url, **kwargs):
        if url == client_assets.INDEX_URL:
            return json.dumps(index).encode()
        if url.startswith(client_assets.KEYS_REPOSITORY):
            return json.dumps(commits).encode()
        return bodies[url.removeprefix(CDN).removesuffix(".json")]
    monkeypatch.setattr(client_assets, "read_url", read)
    previous = client_assets.discover_plan()
    previous["version"] = "3.7.1"
    previous["keys_commit"] = "b" * 40
    previous["id"] = client_assets.plan_id(previous)
    pin = {"version": previous["version"], "tier": "hd", "download_id": previous["id"],
           "keys_commit": previous["keys_commit"]}
    root = tmp_path / "assets" / f"3.7.1-hd-{previous['id'][:16]}"
    for name, value in contents.items():
        path = root / "game" / dests[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    (root / "plan.json").write_text(json.dumps(previous), encoding="utf-8")
    (root / "status.json").write_text(json.dumps({"state": "downloaded", "id": previous["id"]}), encoding="utf-8")
    commits[0]["commit"]["message"] = "[3.7.0]"
    return index, bodies, commits, pin, previous, root


def test_root_packs_reuse_only_database_authorized_verified_workspace(source, tmp_path):
    _, _, _, pin, previous, _ = source
    with pytest.raises(ValueError, match="No version-labelled"):
        client_assets.discover_plan(completed_workspace=tmp_path)
    plan = client_assets.discover_plan(completed_pins=[pin], completed_workspace=tmp_path)
    assert plan["version"] == "3.7.2"
    assert plan["keys_commit"] == previous["keys_commit"]
    assert {item["base_path"] for item in plan["files"]} == {"common/zip", "hd/zip"}
    client_assets.validate_plan(plan)
    assert client_assets.get_live_launcher_info()["version"] == "3.7.2"


def test_worker_and_server_create_identical_plan(source, monkeypatch):
    from wuwa_story_worker import client_assets as worker_assets

    _, _, commits, _, _, _ = source
    commits[0]["commit"]["message"] = "[3.7.2]"
    monkeypatch.setattr(worker_assets, "read_url", client_assets.read_url)
    server_plan = client_assets.discover_plan("3.7.2")
    worker_plan = worker_assets.discover_plan("3.7.2")
    assert server_plan == worker_plan
    worker_assets.validate_plan(server_plan)
    client_assets.validate_plan(worker_plan)


@pytest.mark.parametrize("change", ["metadata_id", "metadata_key", "metadata_tier", "status", "plan",
                                    "missing", "corrupt", "size", "hash", "path", "missing_file"])
def test_reuse_rejects_unverified_or_different_client(source, tmp_path, change):
    index, bodies, _, pin, previous, root = source
    pin = copy.deepcopy(pin)
    if change == "metadata_id":
        pin["download_id"] = "c" * 64
    elif change == "metadata_key":
        pin["keys_commit"] = "c" * 40
    elif change == "metadata_tier":
        pin["tier"] = "sd"
    elif change == "status":
        (root / "status.json").write_text(json.dumps({"state": "failed", "id": previous["id"]}))
    elif change == "plan":
        previous["keys_commit"] = "c" * 40
        (root / "plan.json").write_text(json.dumps(previous))
    elif change == "missing":
        (root / "plan.json").unlink()
    elif change == "corrupt":
        (root / "game/Client/Content/Paks/base.pak").write_bytes(b"evil")
    elif change == "missing_file":
        (root / "game/Client/Content/Paks/base.pak").unlink()
    else:
        manifest = json.loads(bodies["common"])
        field, value = {"size": ("size", 99), "hash": ("md5", "f" * 32),
                        "path": ("dest", "Client/Content/Paks/other.pak")}[change]
        manifest["resource"][0][field] = value
        bodies["common"] = json.dumps(manifest).encode()
        index["resourcePacks"]["common"]["indexFileMd5"] = hashlib.md5(bodies["common"]).hexdigest()
    with pytest.raises(ValueError, match="No version-labelled"):
        client_assets.discover_plan(completed_pins=[pin], completed_workspace=tmp_path)


@pytest.mark.parametrize("pack", ["common", "hd"])
def test_each_manifest_checksum_is_required(source, pack):
    index, _, _, _, _, _ = source
    index["resourcePacks"][pack]["indexFileMd5"] = "f" * 32
    with pytest.raises(RuntimeError, match="checksum"):
        client_assets.discover_plan()


def test_rejects_untrusted_cdn_and_mismatched_versions(source):
    index, _, _, _, _, _ = source
    index["resourcePacks"]["hd"]["version"] = "3.7.1"
    with pytest.raises(ValueError, match="versions do not match"):
        client_assets.discover_plan()
    index["resourcePacks"]["hd"]["version"] = "3.7.2"
    index["cdnList"][0]["url"] = "https://example.com/"
    with pytest.raises(RuntimeError, match="official HTTPS CDN"):
        client_assets.discover_plan()


@pytest.mark.asyncio
async def test_watcher_loads_completed_pin_and_enqueues_new_plan(source, tmp_path, monkeypatch):
    _, _, _, pin, _, _ = source
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enqueue = AsyncMock(return_value={"status": "queued"})
    monkeypatch.setattr(client_updates, "enqueue_client_plan", enqueue)
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("ATTACH DATABASE ':memory:' AS ops"))
            await session.execute(text("CREATE TABLE ops.processor (id INTEGER, key TEXT)"))
            await session.execute(text("CREATE TABLE ops.processing_run (id INTEGER, processor_id INTEGER, status TEXT, metadata JSON)"))
            await session.execute(text("INSERT INTO ops.processor VALUES (1, 'asset_download')"))
            await session.execute(text("INSERT INTO ops.processing_run VALUES (1, 1, 'completed', :metadata)"),
                                  {"metadata": json.dumps({"request": pin})})
            await session.commit()
            result = await client_updates.check_client_update(session, workspace=tmp_path)
            assert result == {"status": "queued"}
            plan = enqueue.await_args.args[1]
            assert plan["version"] == "3.7.2"
            assert plan["keys_commit"] == pin["keys_commit"]
            client_assets.validate_plan(plan)
    finally:
        await engine.dispose()
