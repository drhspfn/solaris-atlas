import asyncio
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import snapshot_jobs
from wuwa_story_worker.broker import JobDeferred
from wuwa_story_worker.client_assets import workspace_lock
from wuwa_story_worker.snapshot_order import earlier_snapshot


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


@pytest.mark.asyncio
async def test_snapshot_waiting_for_database_keeps_retryable_status(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(snapshot_jobs, "_update_admin_run", update)
    monkeypatch.setattr(snapshot_jobs, "_build_and_import_snapshot",
                        AsyncMock(side_effect=JobDeferred("Earlier snapshot is importing")))
    with pytest.raises(JobDeferred):
        await snapshot_jobs.build_and_import_snapshot({"run_id": 42})
    assert update.await_args_list[-1].args == (42, "waiting_dependency")


def test_snapshot_order_is_numeric_and_ignores_current_and_invalid_requests():
    rows = [(42, {"request": {"version": "2.10"}}),
            (43, {"request": {"version": "2.9"}}), (44, {"request": {"version": "broken"}})]
    assert earlier_snapshot(rows, "2.10", 42) == "2.9"
    assert earlier_snapshot(rows, "2.9", 43) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["checkout", "compile"])
async def test_cancellation_retains_os_locks_until_writer_finishes(monkeypatch, tmp_path, phase):
    commit = "a" * 40
    payload = {"schema_version": 1, "job_type": "snapshot.build_import", "version": "1.0",
               "repository_url": "https://example.invalid/data", "commit": commit}
    monkeypatch.setenv("WUWA_WORKER_WORKSPACE", str(tmp_path))
    source = tmp_path / "jobs" / f"1.0-{commit}" / "source"
    source.mkdir(parents=True)
    (source / "README.md").write_text("Game Version: 1.0.0\n", encoding="utf-8")
    started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def writer(*args, **kwargs):
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise TimeoutError("Test did not release snapshot writer")
        finished.set()

    if phase == "checkout":
        monkeypatch.setattr(snapshot_jobs, "prepare_checkout", writer)
        monkeypatch.setattr(snapshot_jobs, "fetch_snapshot", lambda *args: None)
        monkeypatch.setattr(snapshot_jobs.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=""))
    else:
        monkeypatch.setattr(snapshot_jobs, "_checkout_job", AsyncMock(return_value=source))
        monkeypatch.setattr(snapshot_jobs, "compiler_environment", lambda source: {})
        monkeypatch.setattr(snapshot_jobs.subprocess, "run", writer)
    locks = [tmp_path / "job-locks" / f"1.0-{commit}"]
    if phase == "checkout":
        locks.append(tmp_path / "git-cache-lock")
    task = asyncio.create_task(snapshot_jobs._build_and_import_snapshot(payload))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()
        for lock in locks:
            with pytest.raises(BlockingIOError), workspace_lock(lock):
                pytest.fail("A cancelled writer must retain its OS lock")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert finished.is_set()
        for lock in locks:
            with workspace_lock(lock):
                pass
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
