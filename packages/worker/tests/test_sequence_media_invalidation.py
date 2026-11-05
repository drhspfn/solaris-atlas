from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from sqlalchemy.dialects import postgresql
from wuwa_story.read_models import refresh

from wuwa_story_worker.sequence_fragments import invalidate_sequence_quests


@pytest.mark.asyncio
async def test_publication_invalidates_only_endpoint_scoped_quests_in_same_transaction(monkeypatch):
    target = {'owner': 4648369, 'release_id': 21}
    session = SimpleNamespace(scalars=AsyncMock(return_value=[11, 11, 12]), commit=AsyncMock(),
                              get=AsyncMock(return_value=SimpleNamespace(game_version='3.7.0')))
    dirty = AsyncMock()
    monkeypatch.setattr(refresh, 'mark_dirty', dirty)
    assert await invalidate_sequence_quests(session, target) == [11, 12]
    query = str(session.scalars.await_args.args[0].compile(dialect=postgresql.dialect(),
                                                         compile_kwargs={'literal_binds': True}))
    assert 'MATERIALIZED' in query
    assert 'IN (4648369)' in query
    assert 'edge_evidence.release_id = 21' in query
    assert dirty.await_args_list == [call(session, 'quest', [11, 12]),
                                    call(session, 'quest', [11, 12], '3.7.0')]
    session.commit.assert_not_awaited()  # The caller commits publication and dirty marks atomically.


@pytest.mark.asyncio
async def test_unowned_sequence_does_not_queue_a_global_refresh(monkeypatch):
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]))
    dirty = AsyncMock()
    monkeypatch.setattr(refresh, 'mark_dirty', dirty)
    assert await invalidate_sequence_quests(session, {'owner': 4648369, 'release_id': 15}) == []
    dirty.assert_not_awaited()
