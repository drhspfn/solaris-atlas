import os
import subprocess
import sys

import pytest

from wuwa_story_worker.transient_workspace import (
    cleanup_stale_temp_dirs,
    cleanup_transient_workspaces,
    transient_workspace,
)


def test_active_scratch_is_retained_and_normal_exit_cleans_it(tmp_path):
    with transient_workspace(tmp_path) as root:
        (root / "raw.wem").write_bytes(b"temporary")
        assert cleanup_transient_workspaces(tmp_path) == 0
        assert root.exists()
    assert not root.exists()


def test_collector_recovers_a_process_that_did_not_run_finally(tmp_path):
    code = """
import os,sys
from pathlib import Path
from wuwa_story_worker.transient_workspace import transient_workspace
with transient_workspace(Path(sys.argv[1])) as root:
    (root/'raw.wem').write_bytes(b'orphaned')
    os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True, env=os.environ)
    assert cleanup_transient_workspaces(tmp_path) == 1
    assert cleanup_transient_workspaces(tmp_path) == 0


def test_collector_ignores_redirected_entries_and_rejects_redirected_root(tmp_path):
    target = tmp_path / "retained"
    target.mkdir()
    (target / "game.pak").write_bytes(b"keep")
    root = tmp_path / "scratch"
    root.mkdir()
    link = root / ("a" * 32)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    assert cleanup_transient_workspaces(root) == 0
    assert (target / "game.pak").exists()
    with pytest.raises(ValueError, match="redirected"):
        cleanup_transient_workspaces(link)


def test_cleanup_stale_temp_dirs_removes_only_old_solaris_dirs(tmp_path):
    old_dir = tmp_path / ".solaris-model-old"
    fresh_dir = tmp_path / ".solaris-model-diag-fresh"
    other_dir = tmp_path / "unrelated-old"
    for directory in (old_dir, fresh_dir, other_dir):
        directory.mkdir()
        (directory / "file.bin").write_bytes(b"data")
    old_time = 1_000_000.0
    os.utime(old_dir, (old_time, old_time))
    os.utime(other_dir, (old_time, old_time))
    assert cleanup_stale_temp_dirs(tmp_path, max_age_seconds=3600) == 1
    assert not old_dir.exists()
    assert fresh_dir.exists()
    assert other_dir.exists()
