"""Export configured map textures, assemble previews and publish through file storage."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from PIL import Image
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.maps import MapMarker, MapTile, TileMap
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.asset_export import _sha256, export_assets
from wuwa_story_worker.client_assets import save_json, workspace_lock
from wuwa_story_worker.map_catalog import read_catalog
from wuwa_story_worker.map_sources import WORLD_TILE_SIZE, read_tiles

logger = logging.getLogger(__name__)


def assemble_preview(tiles: list[dict], output: Path, maximum: int = 4096) -> dict:
    min_x, max_x = min(t["x"] for t in tiles), max(t["x"] for t in tiles)
    min_y, max_y = min(t["y"] for t in tiles), max(t["y"] for t in tiles)
    with Image.open(tiles[0]["png"]) as first:
        size = first.height
    columns, rows = max_x - min_x + 1, max_y - min_y + 1
    preview_tile = min(size, max(1, maximum // max(columns, rows)))
    canvas = Image.new("RGBA", (columns * preview_tile, rows * preview_tile))
    for tile in tiles:
        with Image.open(tile["png"]) as image:
            if image.height != size:
                raise ValueError(f"Mixed tile sizes: {tile['png']}")
            # MapTileMgr renders every texture in a square UI item, including the
            # two 1028x1024 JH floor textures present in the 3.7 client.
            tile["texture_width"], tile["texture_height"] = image.size
            image = image.convert("RGBA").resize((preview_tile, preview_tile), Image.Resampling.LANCZOS)
            # Preserve RGBA directly; an alpha mask would apply transparency twice.
            canvas.paste(image, ((tile["x"] - min_x) * preview_tile,
                                 (max_y - tile["y"]) * preview_tile))
    canvas.save(output)
    return {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y,
            "tile_size": size, "world_tile_size": WORLD_TILE_SIZE,
            "preview_width": canvas.width, "preview_height": canvas.height}


async def build_maps(root: Path, fmodel: Path, converter: Path, publish: bool = False) -> Path:
    if not converter.is_file():
        raise FileNotFoundError(converter)
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    if plan["version"] != "3.7.0":
        raise ValueError("Map ConfigDB readers and coordinate transforms are verified for 3.7.0 only")
    config_receipt = await export_assets(root, fmodel, "ConfigDB")
    image_receipt = await export_assets(root, fmodel, "UiWorldMap/Image/")
    config = config_receipt.parent / "files/Client/Content/Aki/ConfigDB"
    raw = image_receipt.parent / "files"
    with workspace_lock(root):
        output = root / "maps"
        output.mkdir(exist_ok=True)
        tiles = await asyncio.to_thread(read_tiles, config)
        packages = sorted({tile["source_path"] for tile in tiles})
        for package in packages:
            if not (raw / package).is_file():
                raise FileNotFoundError(f"Configured map texture is missing: {package}")
        package_list = output / "packages.txt"
        # CUE4Parse prefixes loose-file paths with the input directory's name.
        package_list.write_text("\n".join(raw.name + "/" + path for path in packages), encoding="utf-8")
        textures = output / "textures"
        textures.mkdir(exist_ok=True)
        png_root = Path(tempfile.mkdtemp(prefix="decode-", dir=textures))
        log = output / "converter.log"
        logger.info("maps.convert packages=%s version=%s", len(packages), plan["version"])
        def convert() -> None:
            with log.open("w", encoding="utf-8") as stream:
                subprocess.run([str(converter.resolve()), "-i", str(raw.resolve()),
                                "-g", "GAME_WutheringWaves", "-c", str(package_list.resolve()),
                                "-f", "png", "-o", str(png_root.resolve()), "-y"],
                               stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=7200)
        await asyncio.to_thread(convert)
        # The converter can return zero on failed packages. Validate every requested PNG.
        pngs = {}
        for path in png_root.rglob("*.png"):
            relative = path.as_posix().split("/Client/Content/", 1)
            if len(relative) == 2:
                key = "Client/Content/" + relative[1][:-4] + ".uasset"
                if key in pngs:
                    raise ValueError(f"Ambiguous decoded texture: {key}")
                pngs[key] = path
        groups = defaultdict(list)
        for tile in tiles:
            if tile["source_path"] not in pngs:
                raise ValueError(f"Texture decode failed: {tile['source_path']}; inspect {log}")
            tile["png"] = pngs[tile["source_path"]]
            tile["sha256"] = _sha256(tile["png"])
            source = raw / tile["source_path"]
            tile["raw_sha256"] = {path.suffix: _sha256(path) for path in
                                  (source, source.with_suffix(".uexp"), source.with_suffix(".ubulk"))
                                  if path.is_file()}
            groups[tile["map_id"], tile["layer"]].append(tile)
        maps = []
        for (map_id, layer), group in sorted(groups.items()):
            preview = output / f"map-{map_id}-{layer.replace(':', '-')}.png"
            bounds = await asyncio.to_thread(assemble_preview, group, preview)
            maps.append({"game_map_id": map_id, "layer_key": layer, **bounds,
                         "preview": preview.name, "preview_sha256": _sha256(preview), "tiles": [
                             {**tile, "png": tile["png"].relative_to(output).as_posix()}
                             for tile in group]})
        markers, details, diagnostics = await asyncio.to_thread(read_catalog, config, {key[0] for key in groups})
        for data in maps:
            data["catalog"] = details.get(data["game_map_id"], {})
        sources = {}
        for name in ("db_map.db", "db_mapfog.db", "db_ui_resource.db", "db_level_entity.db",
                     "db_area.db", "db_item.db", "db_enrichment.db", "db_map_mark.db", "db_monster_Info.db"):
            sources[name] = {"path": str((config / name).resolve()), "sha256": _sha256(config / name)}
        receipt = output / "manifest.json"
        save_json(receipt, {"schema_version": 1, "asset_job_id": plan["id"],
                            "game_version": plan["version"], "keys_commit": plan["keys_commit"],
                            "converter_sha256": _sha256(converter), "raw_root": str(raw.resolve()),
                            "coordinate_system": "Unreal world units; raster X=world X, raster Y=world Y; tile rows descend",
                            "sources": sources, "maps": maps, "markers": markers, "diagnostics": diagnostics})
        logger.info("maps.built layers=%s tiles=%s markers=%s", len(maps), len(tiles), len(markers))
        if publish:
            await publish_maps(receipt)
        return receipt


async def publish_maps(receipt: Path) -> None:
    bundle = json.loads(receipt.read_text(encoding="utf-8"))
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    storage = S3Storage(settings)
    await storage.ensure_bucket()
    service = FileRegistrationService(storage)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            # Serialize retries for the same immutable client build.
            lock_id = int(bundle["asset_job_id"][:15], 16)
            await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})
            source_ids = {}
            for name, source in bundle["sources"].items():
                path = Path(source["path"])
                if _sha256(path) != source["sha256"]:
                    raise ValueError(f"Map source changed: {name}")
                source_ids[name] = (await service.register_file(session, path, "unknown")).id
            for data in bundle["maps"]:
                preview_path = receipt.parent / data["preview"]
                if _sha256(preview_path) != data["preview_sha256"]:
                    raise ValueError("Map preview changed")
                preview = await service.register_file(session, preview_path, "image", mime_type="image/png")
                values = {key: data[key] for key in ("game_map_id", "layer_key", "tile_size", "world_tile_size", "min_x", "min_y", "max_x", "max_y")}
                values.update(asset_job_id=bundle["asset_job_id"], game_version=bundle["game_version"],
                              preview_file_id=preview.id, metadata_json={"source_file_ids": source_ids,
                              "preview_width": data["preview_width"], "preview_height": data["preview_height"],
                              "keys_commit": bundle["keys_commit"], "converter_sha256": bundle["converter_sha256"],
                              "catalog": data.get("catalog", {})})
                stmt = insert(TileMap).values(**values)
                map_id = await session.scalar(stmt.on_conflict_do_update(
                    index_elements=[TileMap.asset_job_id, TileMap.game_map_id, TileMap.layer_key],
                    set_={("metadata" if key == "metadata_json" else key): value
                          for key, value in values.items()}).returning(TileMap.id))
                for tile in data["tiles"]:
                    png = receipt.parent / tile["png"]
                    if _sha256(png) != tile["sha256"]:
                        raise ValueError("Decoded map tile changed")
                    image = await service.register_file(session, png, "image", mime_type="image/png")
                    raw_path = Path(bundle["raw_root"]) / tile["source_path"]
                    originals = []
                    for suffix, digest in tile["raw_sha256"].items():
                        path = raw_path.with_suffix(suffix)
                        if _sha256(path) != digest:
                            raise ValueError("Raw map tile changed")
                        if path.is_file():
                            original = await service.register_file(session, path, "unknown")
                            originals.append(original.id)
                            await service.register_variant(session, original.id, image.id, "map_texture_png")
                    values = {"map_id": map_id, "x": tile["x"], "y": tile["y"], "file_id": image.id,
                              "source_path": tile["source_path"], "metadata_json": {
                                  key: value for key, value in tile.items() if key != "png"}}
                    values["metadata_json"]["raw_file_ids"] = originals
                    stmt = insert(MapTile).values(**values)
                    await session.execute(stmt.on_conflict_do_update(
                        index_elements=[MapTile.map_id, MapTile.x, MapTile.y],
                        set_={("metadata" if key == "metadata_json" else key): value
                              for key, value in values.items()}))
                logger.info("maps.published map_id=%s tiles=%s", map_id, len(data["tiles"]))
            for start in range(0, len(bundle["markers"]), 500):
                values = [{**marker, "asset_job_id": bundle["asset_job_id"]}
                          for marker in bundle["markers"][start:start + 500]]
                stmt = insert(MapMarker).values(values)
                await session.execute(stmt.on_conflict_do_update(
                    index_elements=[MapMarker.asset_job_id, MapMarker.game_map_id, MapMarker.entity_id],
                    set_={("metadata" if key == "metadata_json" else key):
                          stmt.excluded["metadata" if key == "metadata_json" else key]
                          for key in values[0] if key not in
                          ("asset_job_id", "game_map_id", "entity_id")}))
    finally:
        await engine.dispose()
