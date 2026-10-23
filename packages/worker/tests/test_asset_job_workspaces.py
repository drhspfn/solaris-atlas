import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import asset_jobs


def downloaded(tmp_path, monkeypatch, kind="assets.extract"):
    plan = {"id": "a" * 64, "version": "3.7.0", "tier": "hd"}
    root = tmp_path / "assets" / f"3.7.0-hd-{plan['id'][:16]}"
    (root / "game").mkdir(parents=True)
    (root / "game/client.pak").write_bytes(b"retained")
    (root / "plan.json").write_text(json.dumps(plan))
    (root / "status.json").write_text(json.dumps({"id": plan["id"], "state": "downloaded"}))
    (root / "keys.txt").write_text("fixture")
    monkeypatch.setattr(asset_jobs, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(asset_jobs, "validate_plan", lambda _: None)
    monkeypatch.setattr(asset_jobs, "tool_path", lambda _: tmp_path / "tool")
    return root, {"schema_version": 1, "job_type": kind, "download_id": plan["id"],
                  "version": plan["version"], "tier": plan["tier"], "filter": "ConfigDB"}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["assets.extract", "assets.maps"])
@pytest.mark.parametrize("failure", [False, True])
async def test_queued_exports_are_disposable_after_publication_or_failure(tmp_path, monkeypatch, kind, failure):
    root, payload = downloaded(tmp_path, monkeypatch, kind)
    roots = []

    async def export(scratch, *args, **kwargs):
        roots.append(scratch)
        assert scratch != root and (scratch / "game/client.pak").read_bytes() == b"retained"
        (scratch / "export").write_text("raw")
        if failure:
            raise RuntimeError("publication failed")
        return scratch / "receipt.json"

    monkeypatch.setattr(asset_jobs, "export_assets", export)
    monkeypatch.setattr(asset_jobs, "build_maps", export)
    if failure:
        with pytest.raises(RuntimeError):
            await asset_jobs.extract_client_assets(payload)
    else:
        await asset_jobs.extract_client_assets(payload)
    assert not roots[0].exists()
    assert (root / "game/client.pak").exists() and not (root / "export").exists()


@pytest.mark.asyncio
async def test_shutdown_drains_export_before_deleting_temporary_root(tmp_path, monkeypatch):
    root, payload = downloaded(tmp_path, monkeypatch)
    started, finish = asyncio.Event(), asyncio.Event()
    roots = []

    async def export(scratch, *args, **kwargs):
        roots.append(scratch)
        started.set()
        await finish.wait()
        (scratch / "last-write").write_text("published")

    monkeypatch.setattr(asset_jobs, "export_assets", export)
    task = asyncio.create_task(asset_jobs.extract_client_assets(payload))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert roots[0].exists() and not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not roots[0].exists() and (root / "game/client.pak").exists()


@pytest.mark.asyncio
async def test_download_does_not_schedule_configdb_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("WUWA_ASSET_EXPORT_FILTERS", raising=False)
    monkeypatch.delenv("WUWA_ASSET_BUILD_MAPS", raising=False)
    monkeypatch.setattr(asset_jobs, "download_plan", lambda *args: tmp_path)
    monkeypatch.setattr(asset_jobs, "asset_workspace", lambda: tmp_path)
    publish = AsyncMock()
    monkeypatch.setattr(asset_jobs, "publish_job", publish)
    await asset_jobs.download_client_assets({"id": "a" * 64, "version": "3.7.0", "tier": "hd"})
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_plan_identity_is_validated_before_extracting(tmp_path, monkeypatch):
    root, payload = downloaded(tmp_path, monkeypatch)
    plan = json.loads((root / "plan.json").read_text())
    plan["tier"] = "uhd"
    (root / "plan.json").write_text(json.dumps(plan))
    export = AsyncMock()
    monkeypatch.setattr(asset_jobs, "export_assets", export)
    with pytest.raises(ValueError, match="does not match"):
        await asset_jobs.extract_client_assets(payload)
    export.assert_not_awaited()
