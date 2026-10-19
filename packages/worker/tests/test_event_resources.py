import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from wuwa_story_worker.event_resources import ui_references


@pytest.mark.asyncio
async def test_prefab_export_does_not_hold_catalog_transaction(tmp_path, monkeypatch):
    from wuwa_story_worker import event_resources

    session = AsyncMock()
    event = SimpleNamespace(source_data={"client": {
        "download_id": "pinned", "resources": [{"path": "/Game/UI/Prefabs/Matrix"}],
    }})
    session.scalars.return_value = [event]

    async def extract(*args):
        session.commit.assert_awaited_once()
        session.refresh.assert_not_awaited()
        return [], []

    monkeypatch.setattr(event_resources, "prefab_images", extract)
    monkeypatch.setattr(event_resources, "enqueue_event_images", AsyncMock(return_value={"tasks": 0}))
    _, status = await event_resources.event_resources(
        {"targets": ["activity:1"]},
        {"download_id": "pinned", "asset_version": "3.7.0", "tier": "hd"},
        session, tmp_path,
    )
    assert status == "completed"
    session.refresh.assert_awaited_once_with(event, with_for_update=True)


def test_prefab_references_keep_actual_textures_and_skip_script_audio_and_cycles():
    value = {"Properties": {"ReferenceAssets": [
        {"ObjectName": "Texture2D'ActualMatrixBackground'",
            "ObjectPath": "/Game/UI/Image/Matrix.Matrix"},
        {"ObjectName": "LGUITexturePackerSpriteData'SP_Reward'",
            "ObjectPath": "/Game/UI/Atlas/SP_Reward"},
        {"ObjectName": "PrefabAsset'Child'", "ObjectPath": "/Game/UI/Prefabs/Child"},
        {"ObjectName": "Class'UITexture'", "ObjectPath": "/Script/LGUI"},
        {"ObjectName": "SoundWave'Voice'", "ObjectPath": "/Game/Audio/Voice"},
        {"AssetPathName": "/Game/UI/Prefabs/Child.Child"},
        {"AssetPathName": "/Game/../Secret"},
    ]}}
    assert set(ui_references(value)) == {("image", "/Game/UI/Image/Matrix"),
                                         ("image", "/Game/UI/Atlas/SP_Reward"), ("dependency", "/Game/UI/Prefabs/Child")}


@pytest.mark.asyncio
async def test_dependency_walk_caches_decodes_handles_cycles_and_keeps_partial_results(tmp_path, monkeypatch):
    from wuwa_story_worker import event_resources
    image = "/Game/UI/Image/Matrix"
    roots = {}
    for name, references in [("Root", [
        {"ObjectName": "Texture2D'Bg'", "ObjectPath": image},
        {"ObjectName": "PrefabAsset'Child'", "ObjectPath": "/Game/UI/Prefabs/Child"},
        {"ObjectName": "PrefabAsset'Missing'",
            "ObjectPath": "/Game/UI/Prefabs/Missing"},
    ]), ("Child", [{"ObjectName": "PrefabAsset'Root'", "ObjectPath": "/Game/UI/Prefabs/Root"}])]:
        group = tmp_path / name
        (group / "files").mkdir(parents=True)
        converter = tmp_path / "converter"
        converter.write_bytes(b"tool")
        output = group / ("event-json-" +
                          event_resources._sha256(converter)[:16])
        output.mkdir()
        (output / "references.json").write_text(json.dumps(
            list(event_resources.ui_references(references))), encoding="utf-8")
        roots[name] = group / "manifest.json"

    async def export(root, tool, resource):
        if resource.endswith("Missing"):
            raise RuntimeError("No files exported")
        return roots[resource.rsplit("/", 1)[-1]]
    exporter = AsyncMock(side_effect=export)
    monkeypatch.setattr(event_resources, "export_assets", exporter)
    monkeypatch.setattr(event_resources, "tool_path", lambda _: converter)
    artwork, missing = await event_resources.prefab_images(tmp_path, ["/Game/UI/Prefabs/Root"])
    assert [entry["path"] for entry in artwork] == [image]
    assert missing[0]["expected"] == "/Game/UI/Prefabs/Missing"
    assert exporter.await_count == 3

@pytest.mark.asyncio
async def test_package_inventory_distinguishes_absent_files_and_reuses_completed_scan(tmp_path, monkeypatch):
    from wuwa_story_worker import entity_media, event_resources
    converter = tmp_path / "tool"
    converter.write_bytes(b"tool")
    monkeypatch.setattr(event_resources, "tool_path", lambda _: converter)
    async def scan(command, log, timeout):
        log.write_text("[File] Client/Content/UI/Image/Actual.uasset\n[Done] Listed 1 files\n", encoding="utf-8")
    scan_mock = AsyncMock(side_effect=scan)
    monkeypatch.setattr(entity_media, "tool", scan_mock)
    records = {"1": {"artwork": [{"path": "/Game/UI/Image/Actual"}, {"path": "/Game/UI/Image/Removed"}]}}
    missing = await event_resources.annotate_image_packages(tmp_path, records)
    assert missing == [{"expected": "/Game/UI/Image/Removed", "status": "not_in_client", "matches": []}]
    assert records["1"]["artwork"][0]["package_status"] == "present"
    await event_resources.annotate_image_packages(tmp_path, records)
    assert scan_mock.await_count == 1
