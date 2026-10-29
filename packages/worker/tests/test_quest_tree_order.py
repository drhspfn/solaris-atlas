import sqlite3
from pathlib import Path

import pytest

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
