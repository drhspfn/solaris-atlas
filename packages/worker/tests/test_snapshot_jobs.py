import subprocess
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import snapshot_jobs


@pytest.mark.asyncio
async def test_snapshot_checkout_downloads_into_empty_workspace(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    def git(*args, cwd=upstream):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--initial-branch=1.0")
    (upstream / "README.md").write_text("Game Version: 1.0.0\n", encoding="utf-8")
    git("add", "README.md")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture")
    commit = git("rev-parse", "HEAD")
    workspace = tmp_path / "worker"
    assert not workspace.exists()
    source = await snapshot_jobs._checkout_job(str(upstream), "1.0", commit, workspace)
    assert (source / "README.md").read_text(encoding="utf-8") == "Game Version: 1.0.0\n"
    assert git("rev-parse", "HEAD", cwd=source) == commit
    assert await snapshot_jobs._checkout_job(str(upstream), "1.0", commit, workspace) == source


@pytest.mark.asyncio
async def test_snapshot_job_tracks_running_and_completed(monkeypatch):
    update = AsyncMock()
    summary = {"import_run_id": 7, "records_seen": 120, "records_created": 115}
    build = AsyncMock(return_value=summary)
    monkeypatch.setattr(snapshot_jobs, "_update_admin_run", update)
    monkeypatch.setattr(snapshot_jobs, "_build_and_import_snapshot", build)

    await snapshot_jobs.build_and_import_snapshot({"run_id": 42})

    assert [call.args for call in update.await_args_list] == [(42, "running"), (42, "completed")]
    build.assert_awaited_once_with({"run_id": 42})
    assert update.await_args_list[-1].kwargs["raw_output"] == summary


@pytest.mark.asyncio
async def test_snapshot_job_tracks_failure_and_rethrows(monkeypatch):
    update = AsyncMock()
    build = AsyncMock(side_effect=RuntimeError("compile failed"))
    monkeypatch.setattr(snapshot_jobs, "_update_admin_run", update)
    monkeypatch.setattr(snapshot_jobs, "_build_and_import_snapshot", build)

    with pytest.raises(RuntimeError, match="compile failed"):
        await snapshot_jobs.build_and_import_snapshot({"run_id": 42})

    assert update.await_args_list[0].args == (42, "running")
    assert update.await_args_list[1].args == (42, "failed")
    assert "compile failed" in update.await_args_list[1].kwargs["error"]
