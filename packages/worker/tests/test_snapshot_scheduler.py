from unittest.mock import AsyncMock, Mock

import pytest
from wuwa_story.ingestion.github_snapshots import RemoteSnapshot

from wuwa_story_worker import scheduler


@pytest.mark.asyncio
async def test_snapshot_waits_for_download_then_checks_the_installed_branch(monkeypatch):
    installed = [
        {"version": "3.9.0", "state": "downloading"},
        {"version": "3.8.1", "state": "downloaded"},
    ]
    discover = Mock(return_value=[RemoteSnapshot("3.8", "f" * 40)])
    enqueue = AsyncMock(return_value={"id": 42, "status": "queued"})
    session = AsyncMock()
    session.__aenter__.return_value = session
    monkeypatch.setattr(scheduler, "inspect_installed_clients", lambda _: installed[:1])
    monkeypatch.setattr(scheduler, "discover_remote_snapshots", discover)
    monkeypatch.setattr(scheduler, "enqueue_pinned_snapshot", enqueue)
    monkeypatch.setattr(scheduler, "SessionFactory", lambda: session)

    await scheduler.poll_latest_snapshot()
    discover.assert_not_called()
    enqueue.assert_not_awaited()

    monkeypatch.setattr(scheduler, "inspect_installed_clients", lambda _: installed)
    await scheduler.poll_latest_snapshot()
    discover.assert_called_once_with(scheduler.DEFAULT_REPOSITORY, "3.8", "3.8")
    enqueue.assert_awaited_once_with(session, RemoteSnapshot("3.8", "f" * 40), automatic=True)
