from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import review_order_backfill as module


@pytest.mark.asyncio
@pytest.mark.parametrize('apply', [False, True])
async def test_rebuild_queues_scoped_projections_and_catalog_without_media_jobs(monkeypatch, apply):
    flow = {'Flow': {'FlowListName': 'Story', 'FlowId': 1, 'StateId': 1}}
    session = SimpleNamespace(scalars=AsyncMock(side_effect=[
        [{'QuestId': 100, 'Data': [flow]}, {'QuestId': 200, 'Data': [flow]},
         {'QuestId': 200, 'Data': [flow]}, {'QuestId': 300, 'Data': []}], [10]]), commit=AsyncMock())
    dirty, catalog, graph = AsyncMock(), AsyncMock(
        return_value=(6, 5)), AsyncMock(return_value={'written': 0})
    monkeypatch.setattr(module, 'mark_dirty', dirty)
    monkeypatch.setattr(module, 'rebuild', catalog)
    monkeypatch.setattr(module, 'backfill', graph)
    release = SimpleNamespace(id=21, game_version='3.7.0')
    report = await module.refresh_release(session, release, apply=apply, repair_links=True, current_release_id=21)
    assert report['quest_scopes'] == 1 and report['duplicate_quest_records'] == 1
    graph.assert_awaited_once_with(session, 21, dry_run=not apply)
    if apply:
        assert [call.args[3]
                for call in dirty.await_args_list] == ['3.7.0', 'current']
        assert all(call.args[2] == [10] for call in dirty.await_args_list)
        catalog.assert_awaited_once_with(session, 21)
        session.commit.assert_awaited_once()
    else:
        dirty.assert_not_awaited()
        catalog.assert_not_awaited()
        session.commit.assert_not_awaited()
