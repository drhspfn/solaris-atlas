from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import snapshot_jobs


@pytest.mark.asyncio
async def test_snapshot_job_tracks_running_and_completed(monkeypatch):
    update = AsyncMock()
    build = AsyncMock()
    monkeypatch.setattr(snapshot_jobs, "_update_admin_run", update)
    monkeypatch.setattr(snapshot_jobs, "_build_and_import_snapshot", build)

    await snapshot_jobs.build_and_import_snapshot({"run_id": 42})

    assert [call.args for call in update.await_args_list] == [(42, "running"), (42, "completed")]
    build.assert_awaited_once_with({"run_id": 42})


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
