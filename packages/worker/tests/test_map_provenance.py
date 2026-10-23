import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image
from wuwa_story.storage.local import LocalStorage

from wuwa_story_worker import map_assets, map_provenance


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_map_catalog_and_references_contain_only_durable_game_identity(tmp_path):
    session, service = AsyncMock(), AsyncMock()
    session.add = Mock()
    service.register_file.side_effect = [SimpleNamespace(id=i) for i in (1, 2, 3)]
    service.register_variant.return_value = SimpleNamespace(metadata_json={})
    source = tmp_path / "exports/files/Client/Content/Map/Tile.uasset"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    png = tmp_path / "tile.png"
    Image.new("RGBA", (4, 4), "red").save(png)
    bundle = {"game_version": "3.7.0", "asset_job_id": "a" * 64, "tier": "hd",
              "tools": {"WUWA_FMODEL_PATH": "b" * 64, "WUWA_TEXTURE_CONVERTER_PATH": "c" * 64}}
    publication = map_provenance.MapPublication(session, service, bundle, [])
    await publication.file(source, "unknown")
    await publication.file(png, "image", "image/png")
    provenance = publication.provenance("Client/Content/Map/Tile.uasset", [1])
    publication.reference("map_tile", provenance["package_path"], "tile:0:0", 2, provenance)
    await publication.variant(1, 2, "map_texture_png", provenance)
    await publication.finish(tmp_path / "manifest.json", {"maps": [], "icons": {}, "marker_count": 0}, [1])
    catalog = (tmp_path / "published-catalog.json").read_text()
    assert str(tmp_path) not in catalog and "exports/" not in catalog
    assert json.loads(catalog)["files"]["1"]["package_paths"] == ["Client/Content/Map/Tile.uasset"]
    assert provenance["source_files"] == [{"package_path": "Client/Content/Map/Tile.uasset",
                                           "sha256": digest(source), "file_id": 1}]
    refs = [call.args[0] for call in session.add.call_args_list]
    assert {ref.reference_type for ref in refs} == {"map_tile", "map_catalog"}
    assert all(ref.metadata_json["game_source"]["tools"] == bundle["tools"] for ref in refs)


def repair_fixture(tmp_path):
    storage = LocalStorage(tmp_path / "objects")
    raw = tmp_path / "Tile.uasset"
    raw.write_bytes(b"raw package")
    png = tmp_path / "tile.png"
    Image.new("RGBA", (4, 4), "red").save(png)
    package = "Client/Content/Map/Tile.uasset"
    catalog = {"files": {
        "1": {"id": 1, "sha256": digest(raw), "type": "unknown", "mime": None, "package_paths": [package]},
        "2": {"id": 2, "sha256": digest(png), "type": "image", "mime": "image/png", "package_paths": []}},
        "maps": [{"game_map_id": 8, "layer_key": "gravity:1", "preview_file_id": 2,
                  "tiles": [{"x": 0, "y": 0, "source_path": package, "file_id": 2, "raw_ids": {".uasset": 1}}]}],
        "icons": {}}
    return storage, raw, png, package, catalog


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [1, 2])
async def test_partial_map_repairs_only_missing_package_or_png(tmp_path, monkeypatch, missing):
    storage, raw, png, package, catalog = repair_fixture(tmp_path)
    files = {1: raw, 2: png}
    present = {}
    for identity, path in files.items():
        if identity != missing:
            await storage.put_file(path, str(identity))
            present[identity] = (SimpleNamespace(sha256=bytes.fromhex(digest(path))),
                                 SimpleNamespace(object_key=str(identity)))
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=missing)
    monkeypatch.setattr(map_provenance, "FileRegistrationService", lambda _: service)
    exported = tmp_path / "export/files" / package
    exported.parent.mkdir(parents=True)
    exported.write_bytes(raw.read_bytes())
    export = AsyncMock(return_value=tmp_path / "export/manifest.json")
    decode = AsyncMock(return_value={package: png})
    monkeypatch.setattr(map_provenance, "export_assets", export)
    monkeypatch.setattr(map_assets, "decode_map_packages", decode)
    await map_provenance.repair_maps(AsyncMock(), storage, tmp_path, catalog, present, tmp_path / "tool", tmp_path / "tool")
    if missing == 1:
        assert export.await_args.args[2] == "Client/Content/Map/Tile"
        decode.assert_not_awaited()
    else:
        export.assert_not_awaited()
        assert decode.await_args.args[1] == [package]
    service.register_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_preview_uses_published_tiles_without_raw_extraction(tmp_path, monkeypatch):
    storage, raw, png, package, catalog = repair_fixture(tmp_path)
    atlas = catalog["maps"][0]
    atlas["tiles"].append({**atlas["tiles"][0], "x": 2})
    preview = tmp_path / "preview.png"
    map_assets.assemble_preview([{**tile, "png": png} for tile in atlas["tiles"]], preview)
    atlas["preview_file_id"] = 3
    catalog["files"]["3"] = {"id": 3, "sha256": digest(preview), "type": "image", "mime": "image/png", "package_paths": []}
    present = {}
    for identity, path in ((1, raw), (2, png)):
        await storage.put_file(path, str(identity))
        present[identity] = (SimpleNamespace(sha256=bytes.fromhex(digest(path))), SimpleNamespace(object_key=str(identity)))
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=3)
    monkeypatch.setattr(map_provenance, "FileRegistrationService", lambda _: service)
    export = AsyncMock(side_effect=AssertionError("Preview must reuse published PNGs"))
    monkeypatch.setattr(map_provenance, "export_assets", export)
    await map_provenance.repair_maps(AsyncMock(), storage, tmp_path, catalog, present, tmp_path / "tool", tmp_path / "tool")
    export.assert_not_awaited()
    service.register_file.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_icon_only_requests_its_authored_source(tmp_path, monkeypatch):
    _, _, png, _, catalog = repair_fixture(tmp_path)
    source = "/Game/Map/Marker.Marker"
    catalog["files"] = {"2": catalog["files"]["2"]}
    catalog["maps"] = []
    catalog["icons"] = {source: {"file_id": 2}}
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=2)
    monkeypatch.setattr(map_provenance, "FileRegistrationService", lambda _: service)
    icons = AsyncMock(return_value={source: {"path": str(png)}})
    monkeypatch.setattr(map_provenance, "build_icons", icons)
    await map_provenance.repair_maps(AsyncMock(), None, tmp_path, catalog, {}, tmp_path / "tool", tmp_path / "tool")
    assert icons.await_args.args[3] == [{"metadata_json": {"icon_source": source}}]
    assert icons.await_args.kwargs["exact_textures"] is True
    service.register_file.assert_awaited_once()


@pytest.mark.parametrize("package", ["../escape.uasset", "/absolute.uasset", "C:/windows.uasset", "a\\b.uasset"])
def test_catalog_package_paths_cannot_escape_scratch(tmp_path, package):
    with pytest.raises(ValueError):
        map_provenance.safe_package(tmp_path, package)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_tools", [False, True])
async def test_catalog_reuse_requires_matching_tools_and_verified_complete_rows(tmp_path, monkeypatch, changed_tools):
    plan = {"id": "a" * 64, "version": "3.7.0", "tier": "hd"}
    tools = {"WUWA_FMODEL_PATH": "b" * 64}
    source = {"asset_version": plan["version"], "download_id": plan["id"], "tier": "hd", "tools": tools,
              "map_recipe_version": map_provenance.MAP_RECIPE_VERSION}
    path = f"maps/{plan['id']}/catalog.json"
    stored_source = {**source, "tools": {"WUWA_FMODEL_PATH": "c" * 64} if changed_tools else tools}
    ref = SimpleNamespace(file_id=99, metadata_json={"game_source": {**stored_source,
        "schema_version": 1, "package_path": path,
        "source_files": [{"package_path": "Client/Content/Config.db", "sha256": "a" * 64}]}})
    session = AsyncMock()
    session.scalar.side_effect = [ref, 0]
    session.scalars.side_effect = [[SimpleNamespace(id=8, game_map_id=1, layer_key="gravity:1", preview_file_id=2)],
        [SimpleNamespace(map_id=8, x=0, y=0, source_path="Client/Content/Tile.uasset", file_id=2)], [2]]

    @asynccontextmanager
    async def context():
        yield session

    session.begin = context
    engine = AsyncMock()
    monkeypatch.setattr(map_provenance, "create_async_engine", lambda _: engine)
    monkeypatch.setattr(map_provenance, "async_sessionmaker", lambda *a, **kw: context)
    storage = LocalStorage(tmp_path / "objects")
    monkeypatch.setattr(map_provenance, "S3Storage", lambda _: storage)
    catalog = {"schema_version": 1, "source": source, "marker_count": 0, "files": {"2": {}},
        "maps": [{"game_map_id": 1, "layer_key": "gravity:1", "preview_file_id": 2,
                  "tiles": [{"x": 0, "y": 0, "source_path": "Client/Content/Tile.uasset", "file_id": 2}]}]}
    data = tmp_path / "catalog.json"
    data.write_text(json.dumps(catalog))
    await storage.put_file(data, "catalog")
    verify = AsyncMock(side_effect=[{99: (SimpleNamespace(sha256=bytes.fromhex(digest(data))),
        SimpleNamespace(object_key="catalog"))}, {2: None}])
    monkeypatch.setattr(map_provenance, "registered_files", verify)
    assert await map_provenance.reuse_maps(tmp_path, plan, tools, tmp_path / "tool", tmp_path / "tool") is (not changed_tools)
    assert verify.await_count == (0 if changed_tools else 2)
