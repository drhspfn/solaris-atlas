"""Durable map catalog and repairs of missing blobs without rebuilding intact maps."""

import asyncio
import json
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.maps import MapMarker, MapTile, TileMap
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.ingestion.media_provenance import matches_source
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

from wuwa_story_worker.asset_export import _sha256, export_assets
from wuwa_story_worker.client_assets import save_json
from wuwa_story_worker.map_icons import build_icons
from wuwa_story_worker.media_provenance import package_path

MAP_RECIPE_VERSION = 1


class MapPublication:
    def __init__(self, session, service, bundle, references):
        self.session, self.service, self.bundle = session, service, bundle
        self.references = {(r.reference_type, r.source_path, r.source_name): r for r in references}
        self.files = {}
        self.source = {"asset_version": bundle["game_version"], "download_id": bundle["asset_job_id"],
                       "tier": bundle.get("tier", "hd"), "tools": bundle.get("tools", {}),
                       "map_recipe_version": MAP_RECIPE_VERSION}

    async def file(self, path, kind, mime_type=None):
        file = await self.service.register_file(self.session, path, kind, mime_type=mime_type)
        item = self.files.setdefault(str(file.id), {"id": file.id, "sha256": _sha256(path),
            "type": kind, "mime": mime_type, "package_paths": []})
        if kind == "unknown":
            try:
                package = package_path(path)
            except ValueError:
                package = "Client/Content/Aki/ConfigDB/" + path.name
            if package not in item["package_paths"]:
                item["package_paths"].append(package)
        return file

    def provenance(self, path, ids):
        return {**self.source, "schema_version": 1, "package_path": path,
                "source_files": [{"package_path": self.files[str(identity)]["package_paths"][0],
                                  "sha256": self.files[str(identity)]["sha256"], "file_id": identity}
                                 for identity in ids]}

    def reference(self, kind, path, name, identity, provenance):
        key = kind, path, name
        ref = self.references.get(key)
        if ref is None:
            ref = FileReference(reference_type=kind, source_path=path, source_name=name)
            self.session.add(ref)
            self.references[key] = ref
        ref.file_id = identity
        ref.metadata_json = {"game_source": provenance, "asset_version": self.source["asset_version"]}

    async def variant(self, parent, child, kind, provenance):
        variant = await self.service.register_variant(self.session, parent, child, kind)
        variant.metadata_json = {**(variant.metadata_json or {}), "game_source": provenance}

    async def finish(self, receipt, catalog, source_ids):
        catalog.update(schema_version=1, source=self.source, files=self.files)
        path = receipt.parent / "published-catalog.json"
        save_json(path, catalog)
        file = await self.service.register_file(self.session, path, "raw_json", mime_type="application/json")
        name = f"maps/{self.source['download_id']}/catalog.json"
        self.reference("map_catalog", name, self.source["download_id"], file.id,
                       self.provenance(name, source_ids))


async def registered_files(session, storage, ids):
    rows = list(await session.execute(select(FileObject, FileLocation)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(FileObject.id.in_(ids), FileLocation.backend == storage.backend,
               FileLocation.bucket == storage.bucket)))
    semaphore = asyncio.Semaphore(16)

    async def stat(key):
        async with semaphore:
            return key, await storage.stat(key)

    objects = dict(await asyncio.gather(*(stat(key) for key in {loc.object_key for _, loc in rows})))
    present = {}
    for file, location in rows:
        stored = objects[location.object_key]
        location.available = stored is not None and stored.size_bytes == file.size_bytes
        if location.available:
            present[file.id] = (file, location)
    return present


async def download_file(storage, entry, destination):
    file, location = entry
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as stream:
        async for chunk in storage.get(location.object_key):
            stream.write(chunk)
    if _sha256(destination) != file.sha256.hex():
        raise ValueError("Stored map file checksum mismatch")
    return destination


def safe_package(root, path):
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or "\\" in path or ":" in path:
        raise ValueError("Invalid map package path")
    return root / relative


async def reuse_maps(root, plan, tools, fmodel, converter):
    engine = create_async_engine(get_settings().database_url)
    storage = S3Storage(get_settings())
    source = {"asset_version": plan["version"], "download_id": plan["id"],
              "tier": plan["tier"], "tools": tools, "map_recipe_version": MAP_RECIPE_VERSION}
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(:id)"),
                                  {"id": int(plan["id"][:15], 16)})
            path = f"maps/{plan['id']}/catalog.json"
            ref = await session.scalar(select(FileReference).where(
                FileReference.reference_type == "map_catalog", FileReference.source_path == path,
                FileReference.source_name == plan["id"]))
            if ref is None or not matches_source(ref.metadata_json or {}, source, {"path": path, "kind": "image"}):
                return False
            present = await registered_files(session, storage, [ref.file_id])
            if ref.file_id not in present:
                return False
            work = root / "maps/repair"
            catalog_path = await download_file(storage, present[ref.file_id], work / "catalog.json")
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            if catalog.get("schema_version") != 1 or catalog.get("source") != source:
                return False
            maps = list(await session.scalars(select(TileMap).where(TileMap.asset_job_id == plan["id"])))
            tiles = list(await session.scalars(select(MapTile).where(MapTile.map_id.in_([m.id for m in maps]))))
            map_keys = {m.id: (m.game_map_id, m.layer_key) for m in maps}
            actual = {(map_keys[t.map_id], t.x, t.y, t.source_path, t.file_id) for t in tiles}
            expected = {((m["game_map_id"], m["layer_key"]), t["x"], t["y"], t["source_path"], t["file_id"])
                        for m in catalog["maps"] for t in m["tiles"]}
            previews = {(m.game_map_id, m.layer_key): m.preview_file_id for m in maps}
            expected_previews = {(m["game_map_id"], m["layer_key"]): m["preview_file_id"] for m in catalog["maps"]}
            marker_count = await session.scalar(select(func.count()).select_from(MapMarker).where(
                MapMarker.asset_job_id == plan["id"]))
            if actual != expected or previews != expected_previews or marker_count != catalog["marker_count"]:
                # Missing/incompatible catalog rows need ConfigDB to establish placement.
                return False
            files = {int(key): value for key, value in catalog["files"].items()}
            known = set(await session.scalars(select(FileObject.id).where(FileObject.id.in_(files))))
            if known != files.keys():
                # Deleted file identities require publishing new IDs and relations.
                return False
            present = await registered_files(session, storage, files)
            if set(files) - present.keys():
                await repair_maps(session, storage, root, catalog, present, fmodel, converter)
                if set(files) - (await registered_files(session, storage, files)).keys():
                    raise RuntimeError("Map repair left missing registered files")
            return True
    finally:
        await engine.dispose()


async def repair_maps(session, storage, root, catalog, present, fmodel, converter):
    from wuwa_story_worker.map_assets import assemble_preview, decode_map_packages

    files = {int(key): value for key, value in catalog["files"].items()}
    missing = set(files) - present.keys()
    work, raw = root / "maps/repair", root / "maps/repair/files"
    local = {}
    service = FileRegistrationService(storage)

    async def register(identity, path):
        item = files[identity]
        if _sha256(path) != item["sha256"]:
            raise ValueError("Reconstructed map file differs from pinned publication")
        file = await service.register_file(session, path, item["type"], mime_type=item["mime"])
        if file.id != identity:
            raise ValueError("Reconstructed map file has another registered identity")
        local[identity] = path

    async def fetch(identity, path):
        if identity not in local:
            local[identity] = await download_file(storage, present[identity], path)
        return local[identity]

    # Missing raw files use exact package filters; retained PNGs need no decoding.
    packages = {files[i]["package_paths"][0] for i in missing if files[i]["type"] == "unknown"}
    if packages:
        receipt = await export_assets(root, fmodel, "|".join(sorted(
            {str(PurePosixPath(p).with_suffix("")) if p.endswith((".uasset", ".uexp", ".ubulk")) else p for p in packages})))
        exported = receipt.parent / "files"
        for identity in missing:
            item = files[identity]
            if item["type"] == "unknown":
                await register(identity, safe_package(exported, item["package_paths"][0]))
    decode = {}
    for atlas in catalog["maps"]:
        for tile in atlas["tiles"]:
            if tile["file_id"] not in missing:
                continue
            for suffix, identity in tile["raw_ids"].items():
                package = str(PurePosixPath(tile["source_path"]).with_suffix(suffix))
                path = safe_package(raw, package)
                path.parent.mkdir(parents=True, exist_ok=True)
                source = local.get(identity) or await fetch(identity, work / "downloads" / str(identity))
                path.write_bytes(source.read_bytes())
            decode[tile["source_path"]] = tile["file_id"]
    if decode:
        pngs = await decode_map_packages(raw, sorted(decode), work / "decode", converter)
        for package, identity in decode.items():
            await register(identity, pngs[package])
    icons = {source: icon for source, icon in catalog["icons"].items()
             if icon["file_id"] in missing}
    if icons:
        built = await build_icons(root, fmodel, converter,
            [{"metadata_json": {"icon_source": source}} for source in icons],
            entity_media=True, exact_textures=True)
        for source, icon in icons.items():
            if source not in built:
                raise ValueError(f"Missing map icon during repair: {source}")
            await register(icon["file_id"], Path(built[source]["path"]))
    for atlas in catalog["maps"]:
        identity = atlas["preview_file_id"]
        if identity not in missing or identity in local:
            continue
        tiles = []
        for tile in atlas["tiles"]:
            tile_id = tile["file_id"]
            path = local.get(tile_id) or await fetch(tile_id, work / "tiles" / f"{tile_id}.png")
            tiles.append({"x": tile["x"], "y": tile["y"], "png": path})
        preview = work / f"preview-{identity}.png"
        await asyncio.to_thread(assemble_preview, tiles, preview)
        await register(identity, preview)
    if missing - local.keys():
        raise ValueError("Map catalog contains a file with no repair recipe")
