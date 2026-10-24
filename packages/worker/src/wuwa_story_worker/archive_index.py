"""Disk-backed archive lookup and bounded exact-path FModel filters.

Shared by release and entity extraction. Index creation uses the same external
character-index-lock for every caller; handler imports stay local to that helper.
"""

import asyncio
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath

from wuwa_story_worker.media_diagnostics import iter_archive_paths

VOICE_FILTER_MAX_BYTES = 24 * 1024
VOICE_FILTER_MAX_PATHS = 256


async def ensure_archive_index(archive: Path, fmodel: Path, keys_root: Path) -> Path:
    """List/index an archive once; cancellation must retain the lock through writes."""
    from wuwa_story_worker.client_assets import wait_workspace_lock
    from wuwa_story_worker.entity_media import tool
    from wuwa_story_worker.media_workspace import finish_writes

    marker = archive / "workspace.json"
    if marker.is_file():
        # Patch exports are disposable, but the same pinned client's filename
        # index is shared across image/audio/video preparation and patch retries.
        source = Path(json.loads(marker.read_text(encoding="utf-8"))["source"]).resolve()
        if source != (archive / "game").resolve().parent:
            raise ValueError("Archive index source does not match workspace inputs")
        archive = source
    index = archive / "archive-index.sqlite"
    if index.is_file():
        return index
    async with wait_workspace_lock(archive / "character-index-lock"):
        if not index.is_file():
            log = archive / "archive-index.log"
            await finish_writes(tool([
                fmodel.resolve(), (archive / "game").resolve(),
                "@" + str((keys_root / "keys.txt").resolve()), "--list"], log, 7200))
            await finish_writes(asyncio.to_thread(index_voice_paths, log))
    return index


def index_voice_paths(log: Path) -> Path:
    """Index all supported archive files once, including numeric WEMs and banks."""
    destination = log.with_suffix(".sqlite")
    temporary = destination.with_suffix(".partial.sqlite")
    temporary.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute("CREATE TABLE files (name TEXT, path TEXT PRIMARY KEY)")
            db.executemany("INSERT OR IGNORE INTO files VALUES (?, ?)",
                           ((Path(path).name.casefold(), path) for path in iter_archive_paths(log)))
            db.execute("CREATE INDEX file_name ON files(name)")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def indexed_audio_paths(index: Path, expected) -> list[str]:
    """Resolve exact basenames from any iterable, without rescanning archive logs."""
    expected = sorted({name.casefold() for name in expected})
    paths = set()
    with closing(sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for offset in range(0, len(expected), 500):
            batch = expected[offset:offset + 500]
            paths.update(row[0] for row in db.execute(
                "SELECT path FROM files WHERE name IN (" + ",".join("?" for _ in batch) + ")", batch))
    return sorted(paths)


def indexed_package_paths(index: Path, expected) -> dict[str, str]:
    """Resolve exact authored stems only when both .uasset and .uexp exist.

    Return authored input -> full archive .uasset path, preserving archive case.
    The basename index bounds lookup; full path equality rejects suffix matches
    and packages with the same basename in a different archive directory.
    """
    authored = sorted(set(expected))
    for path in authored:
        if (not re.fullmatch(r"[a-zA-Z0-9_ /-]+", path)
                or PurePosixPath(path).is_absolute() or "/" not in path
                or PurePosixPath(path).as_posix() != path):
            raise ValueError("Expected an authored extensionless Unreal package path")
    names = {PurePosixPath(path).name + suffix for path in authored for suffix in (".uasset", ".uexp")}
    available = {}
    for path in indexed_audio_paths(index, names):
        identity = path.casefold()
        if identity in available and available[identity] != path:
            raise ValueError("Ambiguous case-insensitive archive package path")
        available[identity] = path
    result = {}
    for path in authored:
        stem = ("Client/Content/" + path).casefold()
        if stem + ".uasset" in available and stem + ".uexp" in available:
            result[path] = available[stem + ".uasset"]
    return result


def voice_filter_batches(paths) -> list[list[str]]:
    """Stable exact-path alternatives bounded by both argv bytes and path count."""
    batches, batch, size = [], [], 0
    for path in sorted(set(paths)):
        if (not re.fullmatch(r"[a-zA-Z0-9_ ./-]+", path)
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts):
            raise ValueError("Unsupported authored voice archive path")
        path_size = len(path)  # The validated ASCII alphabet is one UTF-8 byte per character.
        if path_size > VOICE_FILTER_MAX_BYTES:
            raise ValueError("Authored voice archive path exceeds the filter byte budget")
        if batch and (len(batch) == VOICE_FILTER_MAX_PATHS
                      or size + 1 + path_size > VOICE_FILTER_MAX_BYTES):
            batches.append(batch)
            batch, size = [], 0
        size += path_size + bool(batch)  # Include the | separator in the argv budget.
        batch.append(path)
    if batch:
        batches.append(batch)
    return batches
