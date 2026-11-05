"""Import authored tree positions from the same client as its ConfigDB."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from wuwa_story.db.models.core import AuthoredTreeOrder, QuestState
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.ingestion.dungeon_backfill import invalidate_repaired_quests
from wuwa_story.ingestion.dungeon_context import walk
from wuwa_story.ingestion.quest_tree_order import (
    read_tree_node_record,
    read_tree_order,
    tree_node_order_hash,
)


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
                          'record_sha256': hashlib.sha256(raw).hexdigest(),
                          'order_sha256': tree_node_order_hash(records[identity][position.node_id])})
        folder = 'Quests' if kind == 'quest' else 'LevelPlays'
        yield {'asset_version': asset_version, 'download_id': download_id, 'tree_kind': kind, 'tree_id': identity,
               'source_path': f'Client/Content/Aki/Config/UniverseEditorConfig/{folder}/{identity}.bytes',
               'source_sha256': hashlib.sha256(blob).digest(), 'nodes': nodes}


async def store_tree_order(session, records, *, release_id=None):
    records = list(records)
    changed = []
    if records and release_id is not None:
        existing = {(kind, identity, version, download): (digest, nodes)
                    for kind, identity, version, download, digest, nodes in await session.execute(select(
                        AuthoredTreeOrder.tree_kind, AuthoredTreeOrder.tree_id,
                        AuthoredTreeOrder.asset_version, AuthoredTreeOrder.download_id,
                        AuthoredTreeOrder.source_sha256, AuthoredTreeOrder.nodes).where(tuple_(
                            AuthoredTreeOrder.tree_kind, AuthoredTreeOrder.tree_id,
                            AuthoredTreeOrder.asset_version, AuthoredTreeOrder.download_id).in_([
                                (r['tree_kind'], r['tree_id'], r['asset_version'], r['download_id']) for r in records])))}
        changed = [r for r in records if existing.get((r['tree_kind'], r['tree_id'],
            r['asset_version'], r['download_id'])) != (r['source_sha256'], r['nodes'])]
    count = 0
    for record in records:
        statement = insert(AuthoredTreeOrder).values(**record)
        await session.execute(statement.on_conflict_do_update(
            index_elements=['asset_version', 'download_id', 'tree_kind', 'tree_id'],
            set_={'source_path': statement.excluded.source_path,
                  'source_sha256': statement.excluded.source_sha256, 'nodes': statement.excluded.nodes}))
        count += 1
    if changed:
        release = await session.get(GameRelease, release_id)
        if release is None:
            raise ValueError('Unknown tree-order release')
        await invalidate_repaired_quests(session, release, [
            {'from': f'quest:{r["tree_id"]}'} for r in changed if r['tree_kind'] == 'quest'])
        levels = {str(r['tree_id']) for r in changed if r['tree_kind'] == 'level_play'}
        if levels:
            rows = await session.scalars(select(SourceRecord.data).join(SourceFile).where(
                SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
                SourceFile.logical_source_path == 'BinData/LevelPlayNodeData/levelplaynodedata.json',
                func.split_part(SourceRecord.data['Key'].astext, '_', 1).in_(levels)))
            keys = set()
            for row in rows:
                for _, value in walk(row.get('Data')):
                    name, flow, state = (value.get(k) for k in ('FlowListName', 'FlowId', 'StateId'))
                    if isinstance(name, str) and type(flow) is int and type(state) is int:
                        keys.add(f'{name}_{flow}_{state}')
            states = list(await session.scalars(select(QuestState.node_id).join(NodeRevision,
                NodeRevision.node_id == QuestState.node_id).where(
                    NodeRevision.release_id == release_id, QuestState.state_key.in_(keys)))) if keys else []
            if states:
                from wuwa_story.read_models.refresh import mark_dirty
                links = quest_state_links(release_id, state_ids=states)
                quests = sorted(set(await session.scalars(select(links.c.quest_id).distinct())))
                if quests:
                    await mark_dirty(session, 'quest', quests)
                    await mark_dirty(session, 'quest', quests, release.game_version)
    return count
