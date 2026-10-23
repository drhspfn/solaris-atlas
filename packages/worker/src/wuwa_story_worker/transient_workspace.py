"""Collect standalone export scratch after normal completion or a process crash."""

import re
import shutil
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
        try:
            with workspace_lock(directory(locks / root.name)):
                if root.exists():
                    shutil.rmtree(directory(root))
                    removed += 1
        except BlockingIOError:
            continue  # Another process still owns this directory.
    return removed
