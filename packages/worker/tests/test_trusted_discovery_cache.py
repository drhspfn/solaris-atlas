import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import model_uploads
from wuwa_story_worker import trusted_discovery_cache as cache


def test_cache_verifies_game_and_exact_inputs(tmp_path):
    game = tmp_path / "client/game/Client/Content/Paks"
    game.mkdir(parents=True)
    archive = game / "a.pak"
    archive.write_bytes(b"game")
    (tmp_path / "client/keys.txt").write_bytes(b"keys")
    tool = tmp_path / "tools/fmodel"
    tool.parent.mkdir()
    tool.write_bytes(b"tool")
    plan = {"files": [{"path": "Client/Content/Paks/a.pak", "size": 4,
                       "md5": hashlib.md5(b"game").hexdigest()}]}
    forms = [{"id": "mesh-1", "model": "a"}]
    args = (plan, tmp_path / "client", forms, True, [tool])
    key = cache.input_key(*args)
    assert cache.input_key(*args) == key
    assert cache.input_key(plan, args[1], [{**forms[0], "model": "b"}], True, [tool]) != key
    assert cache.input_key(plan, args[1], forms, False, [tool]) != key
    assert cache.input_key({**plan, "id": "new-plan"}, args[1], forms, True, [tool]) != key
    (tmp_path / "client/keys.txt").write_bytes(b"other")
    assert cache.input_key(*args) != key
    (tmp_path / "client/keys.txt").write_bytes(b"keys")
    tool.write_bytes(b"changed")
    assert cache.input_key(*args) != key
    archive.write_bytes(b"evil")
    with pytest.raises(ValueError, match="does not match"):
        cache.input_key(*args)
    archive.write_bytes(b"game")
    (game / "extra.pak").write_bytes(b"extra")
    with pytest.raises(ValueError, match="differ"):
        cache.input_key(*args)


def test_cache_rejects_corrupt_or_oversized_records(tmp_path):
    result = {"component-123": {"component": {}, "model": "Game/Model"}}
    cache.write_record(tmp_path, "key", result)
    assert cache.read_record(tmp_path, "key") == result
    path = tmp_path / "key.json"
    record = json.loads(path.read_bytes())
    record["result"]["component-123"]["model"] = "changed"
    path.write_text(json.dumps(record))
    assert cache.read_record(tmp_path, "key") is None
    path.write_bytes(b"x" * (cache.MAX_RECORD_BYTES + 1))
    assert cache.read_record(tmp_path, "key") is None


def test_cache_bounds_record_count_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "MAX_RECORDS", 2)
    for key in ("one", "two", "three"):
        cache.write_record(tmp_path, key, {})
    assert len(list(tmp_path.glob("*.json"))) == 2
    monkeypatch.setattr(cache, "MAX_RECORD_BYTES", 10)
    cache.write_record(tmp_path, "oversized", {"component": "too long"})
    assert not (tmp_path / "oversized.json").exists()


def test_cache_accepts_current_component_limit(tmp_path):
    result = {f"component-{index:016x}": {"component": {}, "model": "Aki/Test/Model"}
              for index in range(cache.MAX_MODEL_COMPONENTS)}
    cache.write_record(tmp_path, "full", result)
    assert cache.read_record(tmp_path, "full") == result
    result["component-overflow"] = {"component": {}, "model": "Aki/Test/Model"}
    cache.write_record(tmp_path, "overflow", result)
    assert cache.read_record(tmp_path, "overflow") is None


@pytest.mark.asyncio
async def test_retry_reuses_server_discovery_and_rebuilds_corruption(tmp_path, monkeypatch):
    client = tmp_path / "client"
    client.mkdir()
    plan = {"id": "plan", "version": "3.7.0", "tier": "hd"}
    (client / "plan.json").write_text(json.dumps(plan))
    (client / "status.json").write_text(json.dumps({"id": "plan", "state": "downloaded"}))
    monkeypatch.setattr(model_uploads, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(model_uploads, "inspect_installed_clients", lambda _: [
        {"path": str(client), "download_id": "plan", "version": "3.7.0",
         "tier": "hd", "state": "downloaded"}])
    monkeypatch.setattr(model_uploads, "validate_plan", lambda _: None)
    monkeypatch.setattr(model_uploads, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(model_uploads, "input_key",
                        lambda plan, client, forms, animations, tools: str(forms[0]["id"]))
    discover = AsyncMock(return_value={"component-123": {"component": {}, "model": "Game/Model"}})
    monkeypatch.setattr(model_uploads, "_discover_upload_components", discover)
    args = (tmp_path, [{"id": "first"}], "3.7.0", "hd")
    assert await model_uploads.discover_upload_components(*args) == discover.return_value
    assert await model_uploads.discover_upload_components(*args) == discover.return_value
    assert discover.await_count == 1
    (tmp_path / "trusted-model-discovery/first.json").write_text("corrupt")
    await model_uploads.discover_upload_components(*args)
    assert discover.await_count == 2
    await model_uploads.discover_upload_components(tmp_path, [{"id": "second"}], "3.7.0", "hd")
    assert discover.await_count == 3


@pytest.mark.asyncio
async def test_changed_client_during_discovery_is_not_cached(tmp_path, monkeypatch):
    client = tmp_path / "client"
    client.mkdir()
    plan = {"id": "plan", "version": "3.7.0", "tier": "hd"}
    (client / "plan.json").write_text(json.dumps(plan))
    status = client / "status.json"
    status.write_text(json.dumps({"id": "plan", "state": "downloaded"}))
    monkeypatch.setattr(model_uploads, "asset_workspace", lambda: tmp_path)
    monkeypatch.setattr(model_uploads, "inspect_installed_clients", lambda _: [
        {"path": str(client), "download_id": "plan", "version": "3.7.0",
         "tier": "hd", "state": "downloaded"}])
    monkeypatch.setattr(model_uploads, "validate_plan", lambda _: None)
    monkeypatch.setattr(model_uploads, "tool_path", lambda _: tmp_path / "tool")
    monkeypatch.setattr(model_uploads, "input_key", lambda *args: "key")

    async def discover(*args, **kwargs):
        status.write_text(json.dumps({"id": "plan", "state": "failed"}))
        return {}

    monkeypatch.setattr(model_uploads, "_discover_upload_components", discover)
    with pytest.raises(ValueError, match="changed during discovery"):
        await model_uploads.discover_upload_components(tmp_path, [], "3.7.0", "hd")
    assert not (tmp_path / "trusted-model-discovery/key.json").exists()
