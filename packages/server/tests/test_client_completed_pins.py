import copy
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.ingestion import client_assets, client_updates


@pytest.fixture
def upstream(monkeypatch):
    data = {
        "version": "3.7.1",
        "cdn": "https://hw-pcdownload-qcloud.aki-game.net/",
        "base": "client/zip/",
        "manifest": {"resource": [{
            "dest": "Client/Content/Paks/base.pak", "size": 7, "md5": "a" * 32,
        }]},
        "commits": [{"sha": "a" * 40, "commit": {"message": "[3.7.1] Keys update"}}],
    }

    def read(url):
        body = json.dumps(data["manifest"]).encode()
        if url == client_assets.INDEX_URL:
            return json.dumps({"default": {
                "config": {
                    "version": data["version"], "indexFile": "manifest.json",
                    "indexFileMd5": data.get("md5", hashlib.md5(body).hexdigest()),
                    "baseUrl": data["base"],
                },
                "cdnList": [{"url": data["cdn"]}],
            }}).encode()
        if url.startswith(client_assets.KEYS_REPOSITORY):
            return json.dumps(data["commits"]).encode()
        return body

    monkeypatch.setattr(client_assets, "read_url", read)
    plan = client_assets.discover_plan()
    data["commits"][0]["commit"]["message"] = "[3.7.0] Keys update"
    pin = {"version": plan["version"], "tier": plan["tier"],
           "keys_commit": plan["keys_commit"], "download_id": plan["id"]}
    return data, plan, pin


def test_missing_label_reuses_only_the_identical_completed_plan(upstream):
    _, original, pin = upstream
    assert client_assets.discover_plan(completed_pins=[pin]) == original
    with pytest.raises(ValueError, match="No version-labelled AES"):
        client_assets.discover_plan()


@pytest.mark.parametrize("change", [
    "manifest", "file_size", "file_md5", "file_path", "version", "cdn", "base",
    "index_url", "keys_commit", "download_id", "pin_version", "pin_tier", "invalid_commit",
])
def test_completed_pin_rejects_any_changed_identity(upstream, monkeypatch, change):
    data, _, pin = upstream
    if change == "manifest":
        data["manifest"]["extra"] = True
    elif change.startswith("file_"):
        field, value = {
            "file_size": ("size", 8), "file_md5": ("md5", "b" * 32),
            "file_path": ("dest", "Client/Content/Paks/other.pak"),
        }[change]
        data["manifest"]["resource"][0][field] = value
    elif change == "version":
        data["version"] = "3.7.2"
    elif change == "cdn":
        data["cdn"] = "https://hw-pcdownload-aws.aki-game.net/"
    elif change == "base":
        data["base"] = "other/zip/"
    elif change == "index_url":
        monkeypatch.setattr(client_assets, "INDEX_URL", client_assets.INDEX_URL + "?changed=1")
    else:
        field, value = {
            "keys_commit": ("keys_commit", "b" * 40),
            "download_id": ("download_id", "b" * 64),
            "pin_version": ("version", "3.7.0"), "pin_tier": ("tier", "sd"),
            "invalid_commit": ("keys_commit", "latest"),
        }[change]
        pin[field] = value
    with pytest.raises(ValueError, match="No version-labelled AES"):
        client_assets.discover_plan(completed_pins=[pin])


def test_exact_version_label_is_preferred_over_old_completed_pin(upstream):
    data, original, pin = upstream
    data["commits"] = [{"sha": "b" * 40, "commit": {"message": "[3.7.1] Keys update"}}]
    plan = client_assets.discover_plan(completed_pins=[pin])
    assert plan["keys_commit"] == "b" * 40
    assert plan["id"] != original["id"]


def test_completed_pin_does_not_hide_corrupt_manifest(upstream):
    data, _, pin = upstream
    data["md5"] = "0" * 32
    with pytest.raises(RuntimeError, match="Launcher manifest checksum mismatch"):
        client_assets.discover_plan(completed_pins=[pin])


@pytest.mark.asyncio
@pytest.mark.parametrize("status,processor,tier", [
    ("completed", "asset_download", "hd"),
    ("failed", "asset_download", "hd"),
    ("pending", "asset_download", "hd"),
    ("queued", "asset_download", "hd"),
    ("running", "asset_download", "hd"),
    ("completed", "other_processor", "hd"),
    ("completed", "asset_download", "sd"),
])
async def test_poll_authorizes_only_completed_downloads_and_closes_read_transaction(
    upstream, monkeypatch, status, processor, tier,
):
    _, original, pin = upstream
    pin = {**pin, "tier": tier}
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enqueue = AsyncMock(return_value={"id": 20400, "status": "completed"})
    monkeypatch.setattr(client_updates, "enqueue_client_plan", enqueue)
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("ATTACH DATABASE ':memory:' AS ops"))
            await session.execute(text("CREATE TABLE ops.processor (id INTEGER, key TEXT)"))
            await session.execute(text(
                "CREATE TABLE ops.processing_run "
                "(id INTEGER, processor_id INTEGER, status TEXT, metadata JSON)"
            ))
            await session.execute(text("INSERT INTO ops.processor VALUES (1, :key)"),
                                  {"key": processor})
            await session.execute(text(
                "INSERT INTO ops.processing_run VALUES (20400, 1, :status, :metadata)"
            ), {"status": status, "metadata": json.dumps({"request": pin})})
            await session.commit()
            read = client_assets.read_url

            def no_transaction_read(url):
                assert not session.in_transaction()
                return read(url)

            monkeypatch.setattr(client_assets, "read_url", no_transaction_read)
            if (status, processor, tier) == ("completed", "asset_download", "hd"):
                assert await client_updates.check_client_update(session) == {
                    "id": 20400, "status": "completed",
                }
                enqueue.assert_awaited_once_with(session, original)
            else:
                with pytest.raises(ValueError, match="No version-labelled AES"):
                    await client_updates.check_client_update(session)
                enqueue.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_matching_completed_download_is_not_published_again(upstream, monkeypatch):
    _, original, pin = upstream
    plan = client_assets.discover_plan(completed_pins=[pin])
    session = AsyncMock()
    session.scalar.side_effect = [1, ProcessingRun(id=20400, status="completed")]
    publish = AsyncMock()
    monkeypatch.setattr(client_updates, "publish_media_job", publish)
    result = await client_updates.enqueue_client_plan(session, plan)
    assert result["id"] == 20400
    assert result["status"] == "completed"
    assert result["download_id"] == original["id"]
    publish.assert_not_awaited()
    session.flush.assert_not_awaited()


def test_nonmatching_candidate_does_not_prevent_matching_completed_pin(upstream):
    _, original, pin = upstream
    other = copy.deepcopy(pin)
    other["keys_commit"] = "b" * 40
    assert client_assets.discover_plan(completed_pins=[other, pin]) == original
