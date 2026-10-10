"""Persistent, path-only export inventories and exact voice lookup diagnostics."""

import json
import sqlite3
from collections import Counter
from pathlib import Path, PurePosixPath

from wuwa_story.config.settings import get_settings
from wuwa_story.db.session import SessionFactory
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.storage.service import FileRegistrationService

MEDIA_SUFFIXES = {".wem", ".bnk", ".wav", ".ogg", ".mp4", ".png", ".jpg", ".jpeg", ".webp",
                  ".uasset", ".uexp", ".ubulk"}


def iter_archive_paths(log: Path):
    """Read only filenames, never publish CLI output (it can contain AES keys)."""
    complete = False
    with log.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("[File] "):
                value = line[7:].strip().replace("\\", "/")
                path = PurePosixPath(value)
                if not path.is_absolute() and ".." not in path.parts and ":" not in value and path.suffix.casefold() in MEDIA_SUFFIXES:
                    yield value
            elif line.startswith("[Error]"):
                raise ValueError("Archive inventory failed; inspect the worker log")
            elif line.startswith("[Done] Listed "):
                complete = True
    if not complete:
        raise ValueError("Archive inventory did not finish")


def archive_paths(log: Path) -> set[str]:
    return set(iter_archive_paths(log))


def write_inventory(root: Path, log: Path | None, destination: Path, *, exclude: tuple[str, ...] = ()) -> dict:
    # Keep a large client inventory off the heap. Archive and filesystem rows are
    # separate observations, rather than retaining hundreds of thousands of paths.
    indexed_count = exported_count = 0
    extensions = Counter()
    with destination.open("w", encoding="utf-8") as stream:
        for path in iter_archive_paths(log) if log else ():
            indexed_count += 1
            stream.write(json.dumps({"path": path, "origin": "archive"}, ensure_ascii=False) + "\n")
        for directory, subdirs, filenames in root.walk():
            if directory == root:
                subdirs[:] = [name for name in subdirs if name not in exclude]
            for filename in filenames:
                path = directory / filename
                if path.suffix.casefold() not in MEDIA_SUFFIXES or filename.endswith(".partial.wav"):
                    continue
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    # Another publisher can remove a temporary decode during the scan.
                    continue
                exported_count += 1
                extensions[path.suffix.casefold()] += 1
                stream.write(json.dumps({"path": path.relative_to(root).as_posix(), "origin": "export",
                                         "size_bytes": size}, ensure_ascii=False) + "\n")
    return {"indexed_files": indexed_count if log else None, "exported_files": exported_count,
            "extensions": dict(extensions), "scope": "mounted voice archives" if log else "exported media"}


async def publish_inventory(root: Path, log: Path | None = None, *, exclude: tuple[str, ...] = ()) -> dict:
    import asyncio
    import tempfile

    with tempfile.TemporaryDirectory(prefix="media-inventory-") as temporary:
        destination = Path(temporary) / "inventory.jsonl"
        summary = await asyncio.to_thread(write_inventory, root, log, destination, exclude=exclude)
        storage = S3Storage(get_settings())
        await storage.ensure_bucket()
        async with SessionFactory() as session:
            file = await FileRegistrationService(storage).register_file(
                session, destination, "raw_json", mime_type="application/x-ndjson")
            await session.commit()
    return {**summary, "inventory_file_id": file.id}


def voice_report(root: Path, names: set[str], grouped: dict, *,
                 inventory: dict | None = None, config: Path | None = None) -> dict:
    indexed = None
    expected_names = {f"{lang}_{name}{suffix}.wem".casefold() for name in names
                      for lang in ("en", "ja", "ko", "zh") for suffix in ("", "_F", "_M")}
    log = root.parent / "archive-index.log"
    if log.is_file():
        indexed = set()
        for path in iter_archive_paths(log):
            name = PurePosixPath(path).name.casefold()
            if name in expected_names:
                indexed.add(name)
    scene_prefixes = {f"{lang}_{name}".rsplit("_", 1)[0].casefold()
                      for name in names for lang in ("en", "ja", "ko", "zh")}
    by_scene = {}
    for path in root.rglob("*"):
        prefix = path.stem.rsplit("_", 1)[0].casefold()
        if path.suffix.casefold() == ".wem" and prefix in scene_prefixes and path.is_file():
            values = by_scene.setdefault(prefix, [])
            values.append(path.relative_to(root).as_posix())
            values.sort()
            del values[8:]
    configured = None
    if config is not None:
        with sqlite3.connect(config.resolve().as_uri() + "?mode=ro", uri=True) as db:
            ids = [name.removeprefix("vo_") for name in names]
            configured = {row[0] for row in db.execute(
                "SELECT Id FROM plotaudio WHERE Id IN (" + ",".join("?" for _ in ids) + ")", ids)}
    entries = []
    for name in sorted(names):
        for language in ("en", "ja", "ko", "zh"):
            expected = f"{language}_{name}.wem"
            matches = [p.relative_to(root).as_posix() for (lang, _), p in grouped.get(name, {}).items()
                       if lang == language]
            if matches:
                state = "found"
            elif indexed is None:
                state = "not_in_export"
            elif any(value.casefold() in indexed for value in
                     (expected, f"{language}_{name}_F.wem", f"{language}_{name}_M.wem")):
                state = "export_missing"
            else:
                state = "not_in_archives"
            entries.append({"expected": expected, "status": state, "matches": matches,
                            "config_present": name.removeprefix("vo_") in configured if configured is not None else None,
                            "nearby": sorted(by_scene.get(expected[:-4].rsplit("_", 1)[0].casefold(), []))[:8]
                            if not matches else []})
    return {"schema_version": 1, **(inventory or {}), "entries": entries,
            "requested": len(entries), "found": sum(e["status"] == "found" for e in entries),
            "missing": sum(e["status"] != "found" for e in entries)}
