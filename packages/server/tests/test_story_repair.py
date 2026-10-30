from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import story_repair


@pytest.mark.asyncio
@pytest.mark.parametrize('dry_run', [True, False])
async def test_complete_repair_queues_structure_first_and_respects_patch_range(monkeypatch, dry_run):
    session = SimpleNamespace(scalars=AsyncMock(return_value=[
        SimpleNamespace(id=1, game_version='1.0.0', sequence=1),
        SimpleNamespace(id=2, game_version='2.8.0', sequence=2),
        SimpleNamespace(id=3, game_version='3.7.0', sequence=3),
    ]))
    @asynccontextmanager
    async def factory():
        yield session
    monkeypatch.setattr(story_repair, 'SessionFactory', factory)
    repair = AsyncMock(side_effect=lambda *_args, **kwargs: {'dry_run': kwargs['dry_run']})
    queue = AsyncMock(return_value={'queued': 2})
    monkeypatch.setattr(story_repair, 'backfill', repair)
    monkeypatch.setattr(story_repair, 'enqueue_release_media', queue)
    await story_repair.main('2.0.0', '3.7.0', dry_run, False, False, complete=True)
    assert [call.args[1] for call in repair.await_args_list] == [2, 3]
    if dry_run:
        queue.assert_not_awaited()
    else:
        assert [call.args[1] for call in queue.await_args_list] == [2, 3]
        assert all(call.kwargs == {'story_structure_only': True, 'repair_story': True}
                   for call in queue.await_args_list)
