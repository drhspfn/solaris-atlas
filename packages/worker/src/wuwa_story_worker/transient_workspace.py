"""Collect standalone export scratch after normal completion or a process crash."""

import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from wuwa_story_worker.client_assets import workspace_lock
from wuwa_story_worker.tooling import asset_workspace


def directory(path):
    if path.is_symlink() or path.is_junction():
        raise ValueError("Transient workspace directory must not be redirected")
    return path


@contextmanager
def transient_workspace(container: Path):
    container = directory(container)
    container.mkdir(parents=True, exist_ok=True)
    locks = directory(container / ".locks")
    identity = uuid4().hex
    root = container / identity
    # Lock before creating a visible directory: the collector can never mistake
    # a starting writer for an orphan. Keep lock files outside deletable trees.
    with workspace_lock(directory(locks / identity)):
        root.mkdir()
        try:
            yield root
        finally:
            if root.exists():
                shutil.rmtree(directory(root))


def cleanup_transient_workspaces(container: Path | None = None):
    container = directory(container or asset_workspace() / "transient-jobs")
    if not container.exists():
        return 0
    locks = directory(container / ".locks")
    removed = 0
    for root in container.iterdir():
        if (not re.fullmatch(r"[0-9a-f]{32}", root.name)
                or root.is_symlink() or root.is_junction() or not root.is_dir()):
            continue
        lock_file = directory(locks / root.name)
        try:
            with workspace_lock(lock_file):
                if root.exists():
                    shutil.rmtree(directory(root))
                    removed += 1
            try:
                lock_file.unlink(missing_ok=True)
            except OSError:
                pass
        except BlockingIOError:
            continue  # Another process still owns this directory.
    if locks.exists() and not locks.is_symlink() and not locks.is_junction():
        for lock_file in locks.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", lock_file.name) or (container / lock_file.name).exists():
                continue
            try:
                with workspace_lock(directory(lock_file)):
                    pass
                lock_file.unlink(missing_ok=True)
            except (BlockingIOError, OSError):
                continue
    return removed


def cleanup_stale_temp_dirs(
    temp_root: Path | None = None,
    *,
    prefixes: tuple[str, ...] = (".solaris-model-", ".solaris-model-diag-"),
    max_age_seconds: float | None = None,
) -> int:
    """Remove old Solaris temp directories left behind after SIGKILL/OOM crashes."""
    root = directory(temp_root or Path(tempfile.gettempdir()))
    if not root.exists():
        return 0
    ttl = float(os.getenv("WUWA_LOCAL_TEMP_TTL_SECONDS", "21600")) if max_age_seconds is None else max_age_seconds
    cutoff = time.time() - max(ttl, 0.0)
    removed = 0
    for entry in root.iterdir():
        if entry.is_symlink() or entry.is_junction() or not entry.is_dir():
            continue
        if not any(entry.name.startswith(prefix) for prefix in prefixes):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(directory(entry), ignore_errors=True)
            if not entry.exists():
                removed += 1
        except OSError:
            continue
    return removed
