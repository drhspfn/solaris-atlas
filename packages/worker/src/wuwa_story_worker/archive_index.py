"""Disk-backed archive lookup and bounded exact-path FModel filters.

Shared by release and entity extraction; this module never imports either handler.
Callers serialize index creation with their existing workspace execution lock.
"""

import re
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath

from wuwa_story_worker.media_diagnostics import iter_archive_paths

VOICE_FILTER_MAX_BYTES = 24 * 1024
VOICE_FILTER_MAX_PATHS = 256


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


def voice_filter_batches(paths) -> list[list[str]]:
    """Stable exact-path alternatives bounded by both argv bytes and path count."""
    batches, batch, size = [], [], 0
    for path in sorted(set(paths)):
        if (not re.fullmatch(r"[a-zA-Z0-9_./-]+", path)
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
