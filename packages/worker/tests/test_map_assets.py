import hashlib
import json
import os
import struct
from pathlib import Path
from uuid import uuid4

import flatbuffers
import pytest
from PIL import Image

from wuwa_story_worker.map_assets import assemble_preview
from wuwa_story_worker.map_sources import integer, resource_path, table, world_to_pixel


def test_signed_flatbuffer_coordinates_and_absent_fields():
    builder = flatbuffers.Builder(64)
    builder.StartObject(3)
    builder.PrependInt32Slot(0, -123400, 0)
    builder.PrependInt32Slot(1, 567800, 0)
    builder.Finish(builder.EndObject())
    row = table(bytes(builder.Output()))
    assert integer(row, 0) / 100 == -1234
    assert integer(row, 1) / 100 == 5678
    assert integer(row, 2) == 0
    with pytest.raises(struct.error):
        table(b"bad")


def test_world_position_alignment_at_negative_indices():
    # Tile (-1, -2) spans X [-170000,-85000], world Y [170000,255000].
    assert world_to_pixel(-127500, 212500, -1, -2, 1024) == (512, 512)
    assert world_to_pixel(-170000, 170000, -1, -2, 1024) == (0, 0)
    assert world_to_pixel(-85000, 255000, -1, -2, 1024) == (1024, 1024)


def test_assembly_preserves_holes_and_transparency(tmp_path: Path):
    tiles = []
    for x, color in ((-2, (255, 0, 0, 128)), (0, (0, 255, 0, 255))):
        path = tmp_path / f"{x}.png"
        Image.new("RGBA", (4, 4), color).save(path)
        tiles.append({"x": x, "y": -1, "png": path})
    preview = tmp_path / "map.png"
    bounds = assemble_preview(tiles, preview)
    with Image.open(preview) as image:
        assert image.size == (12, 4)
        assert image.getpixel((0, 0)) == (255, 0, 0, 128)
        assert image.getpixel((5, 0)) == (0, 0, 0, 0)
        assert image.getpixel((11, 0)) == (0, 255, 0, 255)
    assert bounds["min_x"] == -2
    assert bounds["max_x"] == 0


def test_resource_paths_reject_traversal():
    assert resource_path("/Game/Aki/Map/T_Tile.T_Tile") == "Client/Content/Aki/Map/T_Tile.uasset"
    with pytest.raises(ValueError):
        resource_path("/Game/../../other.Texture")


def test_raster_rows_follow_game_ui_and_stretch_floor_textures(tmp_path):
    tiles = []
    for y, color, width in ((-2, "red", 4), (-1, "blue", 5)):
        path = tmp_path / f"{y}.png"
        Image.new("RGBA", (width, 4), color).save(path)
        tiles.append({"x": 1, "y": y, "png": path})
    path = tmp_path / "preview.png"
    assemble_preview(tiles, path)
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == (0, 0, 255, 255)
        assert image.getpixel((0, 7)) == (255, 0, 0, 255)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Test PostgreSQL not configured")
async def test_map_publication_is_atomic_and_idempotent(tmp_path, monkeypatch):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from wuwa_story.config.settings import Settings
    from wuwa_story.db.models.maps import MapMarker, MapTile, TileMap
    from wuwa_story.storage.local import LocalStorage

    from wuwa_story_worker import map_assets

    class Storage(LocalStorage):
        backend = "s3"
        bucket = "wuwa"

        async def ensure_bucket(self):
            pass

    settings = Settings(database_url=os.environ["WUWA_TEST_DATABASE_URL"])
    monkeypatch.setattr(map_assets, "get_settings", lambda: settings)
    monkeypatch.setattr(map_assets, "S3Storage", lambda _: Storage(tmp_path / "objects"))
    png = tmp_path / "tile.png"
    Image.new("RGBA", (4, 4), (20, 30, 40, 255)).save(png)
    raw = tmp_path / "raw/Client/Content/Tile.uasset"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"raw unreal fixture")
    source = tmp_path / "config.db"
    source.write_bytes(b"source fixture")
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    job_id = hashlib.sha256(uuid4().bytes).hexdigest()
    bundle = {"asset_job_id": job_id, "game_version": "3.7.0", "keys_commit": "a" * 40,
              "converter_sha256": "b" * 64, "raw_root": str(tmp_path / "raw"),
              "sources": {"config.db": {"path": str(source), "sha256": digest(source)}},
              "maps": [{"game_map_id": 8, "layer_key": "gravity:1", "tile_size": 4,
                        "world_tile_size": 85000, "min_x": -1, "max_x": -1,
                        "min_y": -2, "max_y": -2, "preview_width": 4, "preview_height": 4,
                        "preview": png.name, "preview_sha256": digest(png),
                        "tiles": [{"x": -1, "y": -2, "png": png.name, "sha256": digest(png),
                                   "source_path": "Client/Content/Tile.uasset",
                                   "raw_sha256": {".uasset": digest(raw)}}]}],
              "markers": [{"game_map_id": 8, "entity_id": 777, "category": "chest",
                           "blueprint_type": "Treasure001", "world_x": -127500,
                           "world_y": 212500, "world_z": 50, "metadata_json": {"hidden": False}}]}
    receipt = tmp_path / "manifest.json"
    receipt.write_text(json.dumps(bundle))
    await map_assets.publish_maps(receipt)
    await map_assets.publish_maps(receipt)
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            maps = list(await session.scalars(select(TileMap).where(TileMap.asset_job_id == job_id)))
            assert len(maps) == 1
            assert maps[0].metadata_json["source_file_ids"]
            assert await session.scalar(select(func.count()).select_from(MapTile).where(MapTile.map_id == maps[0].id)) == 1
            assert await session.scalar(select(func.count()).select_from(MapMarker).where(MapMarker.asset_job_id == job_id)) == 1
        # Corruption must fail publication without creating a partial map build.
        bundle["asset_job_id"] = "c" * 64
        receipt.write_text(json.dumps(bundle))
        raw.write_bytes(b"changed raw asset")
        with pytest.raises(ValueError, match="Raw map tile changed"):
            await map_assets.publish_maps(receipt)
        async with async_sessionmaker(engine)() as session:
            assert await session.scalar(select(func.count()).select_from(TileMap).where(TileMap.asset_job_id == "c" * 64)) == 0
    finally:
        await engine.dispose()
