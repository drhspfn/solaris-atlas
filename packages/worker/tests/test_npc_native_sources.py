import sqlite3
from contextlib import closing

import flatbuffers
import pytest

from wuwa_story_worker.npc_native_sources import decode_shop, native_rows


def blob(ints, strings, count):
    b = flatbuffers.Builder(512)
    offsets = {key: b.CreateString(value) for key, value in strings.items()}
    b.StartObject(count)
    for key, value in ints.items():
        b.PrependInt32Slot(key, value, 0)
    for key, offset in offsets.items():
        b.PrependUOffsetTRelativeSlot(key, offset, 0)
    root = b.EndObject()
    b.Finish(root)
    return bytes(b.Output())


def test_native_blueprint_identity_and_source_digest(tmp_path):
    raw = blob({0: 17288}, {1: 'iris', 2: 'Npc'}, 7)
    with closing(sqlite3.connect(tmp_path / 'db_blueprint.db')) as db, db:
        db.execute('CREATE TABLE blueprintconfig(Id INTEGER, BinData BLOB)')
        db.execute('INSERT INTO blueprintconfig VALUES(?, ?)', (17288, raw))
    row, = native_rows(tmp_path, 'db_blueprint.db', 'blueprintconfig', '3.7.1')
    assert row['EntityType'] == 'Npc'
    assert row['_client_source']['asset_version'] == '3.7.1'
    assert len(row['_client_source']['row_sha256']) == 64
    with closing(sqlite3.connect(tmp_path / 'db_blueprint.db')) as db, db:
        db.execute('UPDATE blueprintconfig SET Id=1')
    with pytest.raises(ValueError, match='ID mismatch'):
        list(native_rows(tmp_path, 'db_blueprint.db', 'blueprintconfig', '3.7.1'))


def test_shop_defaults_are_authored_defaults_not_invented_refresh():
    row = decode_shop(blob({0: 10, 1: 140, 3: 1001}, {}, 15), True)
    assert row['ItemNum'] == 1
    assert row['LimitNum'] == -1
    assert row['Show'] is True
    assert 'refresh' not in row


def test_missing_source_does_not_create_sqlite_file(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        list(native_rows(tmp_path, 'db_blueprint.db', 'blueprintconfig', '3.7.1'))
    assert not (tmp_path / 'db_blueprint.db').exists()
