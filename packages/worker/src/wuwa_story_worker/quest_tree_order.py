"""Import authored tree positions from the same client as its ConfigDB."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from wuwa_story.db.models.core import AuthoredTreeOrder
from wuwa_story.ingestion.quest_tree_order import read_tree_node_record, read_tree_order


def tree_records(config_db: Path, files: list[Path], kind: str, asset_version: str, download_id: str, errors=None):
    if (kind not in {'quest', 'level_play'} or not re.fullmatch(r'\d+\.\d+\.\d+', asset_version)
            or not download_id or len(download_id) > 128):
        raise ValueError('A tree kind and exact client version are required')
    table = 'questnodedata' if kind == 'quest' else 'levelplaynodedata'
    selected = {int(path.stem): path for path in files if path.suffix == '.bytes' and path.stem.isdecimal()}
    records: dict[int, dict[int, dict]] = {identity: {} for identity in selected}
    with sqlite3.connect(config_db.resolve().as_uri() + '?mode=ro', uri=True) as db:
        for key, blob in db.execute(f'SELECT Key, BinData FROM {table}'):
            tree, separator, node = key.partition('_')
            if not separator or not tree.isdecimal() or not node.isdecimal() or int(tree) not in records:
                continue
            actual_key, data = read_tree_node_record(blob)
            if actual_key != key:
                raise ValueError('Client node key does not match its ConfigDB record')
            records[int(tree)][int(node)] = data
    for identity, path in sorted(selected.items()):
        try:
            blob = path.read_bytes()
            positions = read_tree_order(blob, kind, records[identity], tree_id=identity)
        except (ValueError, OSError) as error:
            if errors is None:
                raise
            errors.append({'kind': kind, 'tree_id': identity, 'error': str(error)})
            continue
        nodes = []
        for position in positions:
            raw = json.dumps(records[identity][position.node_id], sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False).encode('utf-8')
            nodes.append({'id': position.node_id, 'parent_id': position.parent_id,
                          'type': position.node_type, 'path': list(position.path),
                          'record_sha256': hashlib.sha256(raw).hexdigest()})
        folder = 'Quests' if kind == 'quest' else 'LevelPlays'
        yield {'asset_version': asset_version, 'download_id': download_id, 'tree_kind': kind, 'tree_id': identity,
               'source_path': f'Client/Content/Aki/Config/UniverseEditorConfig/{folder}/{identity}.bytes',
               'source_sha256': hashlib.sha256(blob).digest(), 'nodes': nodes}


async def store_tree_order(session, records):
    count = 0
    for record in records:
        statement = insert(AuthoredTreeOrder).values(**record)
        await session.execute(statement.on_conflict_do_update(
            index_elements=['asset_version', 'download_id', 'tree_kind', 'tree_id'],
            set_={'source_path': statement.excluded.source_path,
                  'source_sha256': statement.excluded.source_sha256, 'nodes': statement.excluded.nodes}))
        count += 1
    return count
