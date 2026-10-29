import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from wuwa_story_worker import release_media
from wuwa_story_worker.quest_tree_order import tree_records

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
    session.get = AsyncMock(return_value=MagicMock(tree_count=25))
    session.commit = AsyncMock()
    parent = {'asset_version': '3.7.0', 'download_id': 'client-123'}
    result, status = await release_media.import_tree_orders(parent, session)
    assert status == 'completed' and result['reused'] and result['trees'] == 25
    import_index.assert_not_awaited()

    session.get.return_value = None
    _, status = await release_media.import_tree_orders(parent, session)
    assert status == 'partial'
    session.add.assert_not_called()
