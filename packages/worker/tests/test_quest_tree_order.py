import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from wuwa_story_worker import release_media
from wuwa_story_worker.quest_tree_order import store_tree_order, tree_records

FIXTURES = Path(__file__).parents[2] / 'server/tests/fixtures'


def test_import_uses_same_client_node_table_and_preserves_source_paths(tmp_path):
    database = tmp_path / 'db_QuestNodeData.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE questnodedata (Key TEXT PRIMARY KEY, BinData BLOB)')
        for identity in range(1, 5):
            blob = (FIXTURES / f'quest_node_{identity}_synthetic.bin').read_bytes()
            db.execute('INSERT INTO questnodedata VALUES (?, ?)', (f'42_{identity}', blob))
    source = tmp_path / '42.bytes'
    source.write_bytes((FIXTURES / 'quest_tree_synthetic.bin').read_bytes())
    record = next(tree_records(database, [source], 'quest', '3.7.0', 'client-123'))
    assert record['asset_version'] == '3.7.0'
    assert record['download_id'] == 'client-123'
    assert record['source_path'].endswith('/Quests/42.bytes')
    assert [(node['id'], node['path']) for node in record['nodes']] == [
        (1, []), (2, [0]), (3, [1]), (4, [1, 0]),
    ]
    with sqlite3.connect(database) as db:
        db.execute('DELETE FROM questnodedata WHERE Key = ?', ('42_4',))
    with pytest.raises(ValueError, match='pinned client node table'):
        list(tree_records(database, [source], 'quest', '3.7.0', 'client-123'))


@pytest.mark.asyncio
async def test_completed_client_index_is_reused_and_partial_import_is_not_marked(monkeypatch):
    @asynccontextmanager
    async def context(*args):
        yield MagicMock()

    monkeypatch.setattr(release_media, 'engine', MagicMock(connect=context))
    monkeypatch.setattr(release_media, 'job_lock', context)
    import_index = AsyncMock(return_value=({'trees': 2, 'unresolved': 1}, 'partial'))
    monkeypatch.setattr(release_media, '_import_tree_orders', import_index)
    session = MagicMock()
    session.get = AsyncMock(return_value=MagicMock(tree_count=25, schema_version=3))
    session.commit = AsyncMock()
    parent = {'asset_version': '3.7.0', 'download_id': 'client-123'}
    result, status = await release_media.import_tree_orders(parent, session)
    assert status == 'completed' and result['reused'] and result['trees'] == 25
    import_index.assert_not_awaited()

    session.get.return_value = None
    _, status = await release_media.import_tree_orders(parent, session)
    assert status == 'partial'
    session.add.assert_not_called()

    old_index = MagicMock(tree_count=25, schema_version=1)
    session.get.return_value = old_index
    import_index.return_value = ({'trees': 28, 'unresolved': 0}, 'completed')
    _, status = await release_media.import_tree_orders(parent, session)
    assert status == 'completed'
    assert old_index.schema_version == 3 and old_index.tree_count == 28


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['quest', 'level_play'])
async def test_changed_tree_marks_only_release_evidenced_owners_before_batch_commit(monkeypatch, kind):
    from wuwa_story.read_models import refresh

    record = {'tree_kind': kind, 'tree_id': 42, 'asset_version': '3.7.1',
              'download_id': 'client', 'source_path': 'Client/42.bytes',
              'source_sha256': b'changed', 'nodes': []}
    values = [[101, 101]] if kind == 'quest' else [
        [{'Key': '42_66', 'Data': {'Condition': {'Type': 'PlayFlow', 'Flow': {
            'FlowListName': 'Story', 'FlowId': 28, 'StateId': 1}}}}], [900], [101, 101]]
    session = SimpleNamespace(execute=AsyncMock(return_value=[]), scalars=AsyncMock(side_effect=values),
        get=AsyncMock(return_value=SimpleNamespace(id=21, game_version='3.7.0')), commit=AsyncMock())
    dirty = AsyncMock()
    monkeypatch.setattr(refresh, 'mark_dirty', dirty)
    assert await store_tree_order(session, [record], release_id=21) == 1
    assert dirty.await_args_list == [call(session, 'quest', [101]), call(session, 'quest', [101], '3.7.0')]
    session.commit.assert_not_awaited()  # Tree write and marks share the caller's batch transaction.
    if kind == 'quest':
        query = session.scalars.await_args.args[0].compile()
        assert [42] in query.params.values()
        assert 21 in query.params.values()
    else:
        query = session.scalars.await_args_list[0].args[0].compile()
        assert 'source_file.release_id' in str(query)
        assert ['42'] in query.params.values() or {'42'} in query.params.values()
        endpoint_query = session.scalars.await_args_list[-1].args[0].compile()
        assert [900] in endpoint_query.params.values()


@pytest.mark.asyncio
async def test_unchanged_tree_does_not_increment_projection_generations(monkeypatch):
    from wuwa_story.read_models import refresh

    record = {'tree_kind': 'quest', 'tree_id': 42, 'asset_version': '3.7.1',
              'download_id': 'client', 'source_path': 'Client/42.bytes',
              'source_sha256': b'same', 'nodes': []}
    session = SimpleNamespace(execute=AsyncMock(side_effect=[[
        ('quest', 42, '3.7.1', 'client', b'same', [])], []]), get=AsyncMock())
    dirty = AsyncMock()
    monkeypatch.setattr(refresh, 'mark_dirty', dirty)
    assert await store_tree_order(session, [record], release_id=21) == 1
    session.get.assert_not_awaited()
    dirty.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_repair_invalidates_only_exact_quest_owner_keys(monkeypatch):
    from wuwa_story.ingestion.dungeon_backfill import invalidate_repaired_quests
    from wuwa_story.read_models import refresh

    session = SimpleNamespace(scalars=AsyncMock(return_value=[101, 101]), commit=AsyncMock())
    release = SimpleNamespace(id=15, game_version='3.1.0')
    dirty = AsyncMock()
    monkeypatch.setattr(refresh, 'mark_dirty', dirty)
    assert await invalidate_repaired_quests(session, release, [
        {'from': 'quest_node:114000031:231'}, {'from': 'quest:114000031'},
        {'from': 'flow_state:Story_28_1'}, {'from': 'quest_node:wrong:1'},
    ]) == [101]
    query = session.scalars.await_args.args[0].compile()
    assert [114000031] in query.params.values()
    assert 15 in query.params.values()
    assert dirty.await_args_list == [call(session, 'quest', [101]), call(session, 'quest', [101], '3.1.0')]
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sequence_graph_repair_resolves_only_pinned_state_endpoints(monkeypatch):
    from wuwa_story.ingestion.dungeon_backfill import invalidate_repaired_quests
    from wuwa_story.read_models import refresh

    session = SimpleNamespace(scalars=AsyncMock(side_effect=[[900], [101, 101]]))
    dirty = AsyncMock()
    monkeypatch.setattr(refresh, 'mark_dirty', dirty)
    assert await invalidate_repaired_quests(session, SimpleNamespace(id=15, game_version='3.1.0'),
        [{'from': 'action:Story_28_1:1', 'state_key': 'Story_28_1'}]) == [101]
    state_query = session.scalars.await_args_list[0].args[0].compile()
    assert ['Story_28_1'] in state_query.params.values() and 15 in state_query.params.values()
    endpoint_query = session.scalars.await_args_list[1].args[0].compile()
    assert [900] in endpoint_query.params.values()
    assert dirty.await_args_list == [call(session, 'quest', [101]), call(session, 'quest', [101], '3.1.0')]
