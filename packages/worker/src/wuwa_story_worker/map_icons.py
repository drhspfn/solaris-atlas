"""Decode item textures and LGUI atlas sprites used by world-map markers."""

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from wuwa_story_worker.asset_export import _sha256, export_assets


def sprite_box(info: dict, width: int, height: int) -> tuple[int, int, int, int]:
    x = sorted((round(info["uv0X"] * width), round(info["uv3X"] * width)))
    y = sorted((round(info["uv0Y"] * height), round(info["uv3Y"] * height)))
    if not (0 <= x[0] < x[1] <= width and 0 <= y[0] < y[1] <= height):
        raise ValueError("Atlas sprite lies outside its texture")
    return x[0], y[0], x[1], y[1]


async def build_icons(
    root: Path, fmodel: Path, converter: Path, markers: list[dict], *, entity_media: bool = False
) -> dict:
    sources = {m["metadata_json"].get("icon_source") for m in markers} - {None, ""}
    cache = None
    if entity_media:
        identity = json.dumps(
            [sorted(sources), _sha256(converter), _sha256(fmodel)], sort_keys=True
        )
        cache = (
            root / "entity-image-cache" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
        )
        if cache.is_file():
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if all(
                Path(path).is_file() and _sha256(Path(path)) == digest
                for path, digest in saved["files"].items()
            ):
                return saved["icons"]
    folders = set()
    for source in sources:
        if source.startswith("/Game/Aki/UI/UIResources/"):
            folder = source.removeprefix("/Game/Aki/UI/UIResources/").rsplit("/", 1)[0] + "/"
            if (
                entity_media
                or folder.startswith("Common/Image/")
                or folder.startswith("Common/Atlas/WorldMapIcon")
                or folder == "UiWorldMap/Atlas/MoraleMapIcon/"
            ):
                folders.add(folder)
    output = root / "maps/icons"
    output.mkdir(parents=True, exist_ok=True)
    pngs = {}
    importers = []
    raw_files = {}
    folders = {
        folder
        for folder in folders
        if not any(folder.startswith(other) and folder != other for other in folders)
    }
    for folder in sorted(folders):
        receipt = await export_assets(root, fmodel, folder)
        raw = receipt.parent / "files"
        paths = list(raw.rglob("*.uasset"))
        for path in paths:
            key = "/Game/" + path.relative_to(raw / "Client/Content").with_suffix("").as_posix()
            raw_files[key] = path
        decoded = (
            (output / ("entity-" + receipt.parent.name + "-" + _sha256(converter)[:16]))
            if entity_media
            else Path(tempfile.mkdtemp(prefix=receipt.parent.name + "-", dir=output))
        )
        decoded.mkdir(exist_ok=True)
        complete = decoded / "decode-receipt.json"
        cached = False
        if entity_media and complete.exists():
            cached = all(
                (decoded / path).is_file() and _sha256(decoded / path) == digest
                for path, digest in json.loads(complete.read_text(encoding="utf-8")).items()
            )
        packages = decoded / "packages.txt"
        textures = [p for p in paths if not p.name.startswith(("SP_", "TPI_"))]
        packages.write_text(
            "\n".join(raw.name + "/" + p.relative_to(raw).as_posix() for p in textures),
            encoding="utf-8",
        )

        def convert(raw=raw, packages=packages, decoded=decoded, paths=paths, textures=textures):
            # CUE collects loaded objects until ExportSession runs. Bound each
            # subprocess instead of retaining an entire texture folder in RAM.
            environment = {**os.environ, "DOTNET_PROCESSOR_COUNT": "2"}
            with (decoded / "converter.log").open("w", encoding="utf-8") as log:
                for extension, selected in (
                    ("png", textures),
                    ("json", [p for p in paths if p.name.startswith("TPI_")]),
                ):
                    for offset in range(0, len(selected), 8):
                        batch = selected[offset:offset + 8]
                        packages.write_text(
                            "\n".join(raw.name + "/" + p.relative_to(raw).as_posix() for p in batch),
                            encoding="utf-8",
                        )
                        subprocess.run(
                            [str(converter.resolve()), "-i", str(raw.resolve()),
                             "-g", "GAME_WutheringWaves", "-c", str(packages.resolve()),
                             "-f", extension, "-o", str(decoded.resolve()), "-y"],
                            stdout=log, stderr=subprocess.STDOUT, timeout=1800,
                            check=True, env=environment,
                        )

        if not cached:
            await asyncio.to_thread(convert)
            if entity_media:
                hashes = {
                    p.relative_to(decoded).as_posix(): _sha256(p)
                    for p in decoded.rglob("*")
                    if p.is_file() and p.suffix in (".png", ".json") and p != complete
                }
                complete.write_text(json.dumps(hashes), encoding="utf-8")
        for path in decoded.rglob("*.png"):
            relative = path.relative_to(decoded / raw.name / "Client/Content")
            pngs["/Game/" + relative.with_suffix("").as_posix()] = path
        importers.extend(decoded.rglob("TPI_*.json"))
    result = {}
    for source in sources:
        key = source.split(".", 1)[0]
        if key in pngs:
            result[source] = {
                "path": str(pngs[key].resolve()),
                "sha256": _sha256(pngs[key]),
                "raw_paths": [
                    str(p.resolve())
                    for p in [
                        raw_files[key],
                        raw_files[key].with_suffix(".uexp"),
                        raw_files[key].with_suffix(".ubulk"),
                    ]
                    if p.is_file()
                ],
            }
    for importer in importers:
        for export in json.loads(importer.read_text(encoding="utf-8-sig")):
            data = export.get("Properties", {})
            if not data.get("Sprites"):
                continue
            atlases = {int(x["Key"]): x["Value"] for x in data["SpriteAtlasTextureMap"]}
            infos = {int(x["Key"]): x["Value"] for x in data["SpriteInfoMap"]}
            for index, source in enumerate(data["Sprites"]):
                if source not in sources or index not in infos or index not in atlases:
                    continue
                textures = data.get("SoftAtlasTextures") or data.get("AtlasTextures", [])
                reference = textures[atlases[index]]
                texture = (reference.get("AssetPathName") or reference["ObjectPath"]).split(".", 1)[
                    0
                ]
                if texture not in pngs:
                    print(f"WARNING: Missing atlas texture: {texture}")
                    continue
                with Image.open(pngs[texture]) as image:
                    cropped = image.crop(sprite_box(infos[index], *image.size))
                    path = output / (source.rsplit("/", 1)[-1].split(".")[0] + ".png")
                    cropped.save(path)
                raw = raw_files[texture]
                result[source] = {
                    "path": str(path.resolve()),
                    "sha256": _sha256(path),
                    "raw_paths": [
                        str(p.resolve())
                        for p in [
                            raw,
                            raw.with_suffix(".uexp"),
                            raw_files.get(source.split(".", 1)[0]),
                            raw_files.get(source.split(".", 1)[0]).with_suffix(".uexp")
                            if raw_files.get(source.split(".", 1)[0])
                            else None,
                        ]
                        if p and p.is_file()
                    ],
                }
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        files = {str(Path(icon["path"])): icon["sha256"] for icon in result.values()}
        files.update(
            {path: _sha256(Path(path)) for icon in result.values() for path in icon["raw_paths"]}
        )
        temporary = cache.with_suffix(".partial.json")
        temporary.write_text(json.dumps({"icons": result, "files": files}), encoding="utf-8")
        temporary.replace(cache)
    return result
