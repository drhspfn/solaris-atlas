"""Client update queue reuse, admin authorization and automatic tier selection."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wuwa_story.ingestion import client_updates

from wuwa_story_worker import client_updates as worker_updates


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['queued', 'running', 'completed'])
async def test_update_does_not_publish_duplicate_work(monkeypatch, status):
    db = AsyncMock()
    db.scalar.side_effect = [1, SimpleNamespace(id=3, status=status)]
    publish = AsyncMock()
    monkeypatch.setattr(client_updates, 'publish_media_job', publish)
    result = await client_updates.enqueue_client_plan(db, {'id': 'a'*64, 'version': '3.7.1', 'tier': 'hd', 'files': []})
    assert result['status'] == status
    publish.assert_not_awaited()

@pytest.mark.asyncio
async def test_periodic_update_checks_each_installed_tier_once(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_updates, 'inspect_installed_clients', lambda _: [
        {'state':'downloaded','tier':'hd'}, {'state':'downloaded','tier':'hd'},
        {'state':'failed','tier':'sd'}, {'state':'downloaded','tier':'uhd'}])
    db = AsyncMock()
    db.__aenter__.return_value = db
    monkeypatch.setattr(worker_updates, 'SessionFactory', lambda: db)
    check = AsyncMock(return_value={'status':'queued'})
    monkeypatch.setattr(worker_updates, 'check_client_update', check)
    await worker_updates.poll_client_updates(tmp_path)
    assert [c.args[1] for c in check.await_args_list] == ['hd','uhd']

@pytest.mark.asyncio
async def test_admin_update_uses_same_shared_updater(monkeypatch):
    from wuwa_story.api.routes import admin_data
    check = AsyncMock(return_value={'status':'completed'})
    monkeypatch.setattr(admin_data,'check_client_update',check)
    db=AsyncMock()
    assert await admin_data.update_client(admin_data.ClientUpdateRequest(tier='sd'),db) == {'status':'completed'}
    check.assert_awaited_once_with(db,'sd')
