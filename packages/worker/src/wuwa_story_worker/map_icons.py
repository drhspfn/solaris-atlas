"""Decode item textures and LGUI atlas sprites used by world-map markers."""

import asyncio
import json
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


async def build_icons(root: Path, fmodel: Path, converter: Path, markers: list[dict]) -> dict:
    sources = {m["metadata_json"].get("icon_source") for m in markers} - {None, ""}
    folders = set()
    for source in sources:
        if source.startswith("/Game/Aki/UI/UIResources/"):
            folder = source.removeprefix("/Game/Aki/UI/UIResources/").rsplit("/", 1)[0] + "/"
            if (
                folder.startswith("Common/Image/")
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
        decoded = Path(tempfile.mkdtemp(prefix=receipt.parent.name + "-", dir=output))
        packages = decoded / "packages.txt"
        textures = [p for p in paths if not p.name.startswith(("SP_", "TPI_"))]
        packages.write_text(
            "\n".join(raw.name + "/" + p.relative_to(raw).as_posix() for p in textures),
            encoding="utf-8",
        )

        def convert(raw=raw, packages=packages, decoded=decoded, paths=paths):
            with (decoded / "converter.log").open("w", encoding="utf-8") as log:
                subprocess.run(
                    [
                        str(converter.resolve()),
                        "-i",
                        str(raw.resolve()),
                        "-g",
                        "GAME_WutheringWaves",
                        "-c",
                        str(packages.resolve()),
                        "-f",
                        "png",
                        "-o",
                        str(decoded.resolve()),
                        "-y",
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=1800,
                    check=True,
                )
                if any(p.name.startswith("TPI_") for p in paths):
                    subprocess.run(
                        [
                            str(converter.resolve()),
                            "-i",
                            str(raw.resolve()),
                            "-g",
                            "GAME_WutheringWaves",
                            "-p",
                            "*TPI_*",
                            "-f",
                            "json",
                            "-o",
                            str(decoded.resolve()),
                            "-y",
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=1800,
                        check=True,
                    )

        await asyncio.to_thread(convert)
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
                    raise ValueError(f"Missing atlas texture: {texture}")
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
    return result
