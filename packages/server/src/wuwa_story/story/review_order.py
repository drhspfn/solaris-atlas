"""Snapshot-local archive sequence, distinct from a runtime quest-tree path."""

from collections import Counter

from sqlalchemy import select

from wuwa_story.db.models.core import QuestState
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.ingestion.dungeon_context import decoded

SOURCE = 'BinData/PlotHandBook/plothandbookconfig.json'


def review_positions(raw):
    """Keep authored list slots; repeated flows have no single display position."""
    items = decoded(raw)
    if not isinstance(items, list):
        return {}
    keys = []
    for index, item in enumerate(items):
        flow = item.get('Flow') if isinstance(item, dict) else None
        if not isinstance(flow, dict):
            continue
        name, group, state = (flow.get(field)
                              for field in ('FlowListName', 'FlowId', 'StateId'))
        if (isinstance(name, str) and name and type(group) is int and group > 0
                and type(state) is int and state > 0):
            keys.append((f'{name}_{group}_{state}', index))
    counts = Counter(key for key, _ in keys)
    return {key: (index,) for key, index in keys if counts[key] == 1}


async def quest_review_order(session, quest_id, release_id):
    if release_id is None:
        return None
    rows = list(await session.execute(select(SourceRecord.id, SourceRecord.row_index, SourceRecord.data)
                                      .join(SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        SourceFile.logical_source_path == SOURCE,
        SourceRecord.data['QuestId'].as_integer() == quest_id)))
    if len(rows) != 1:
        return None
    record_id, row_index, raw = rows[0]
    positions = review_positions(raw.get('Data'))
    if not positions:
        return None
    states = dict((await session.execute(select(QuestState.state_key, QuestState.node_id)
                                         .join(NodeRevision, NodeRevision.node_id == QuestState.node_id).where(
        NodeRevision.release_id == release_id, QuestState.state_key.in_(positions)).distinct())).all())
    positions = {key: position for key,
                 position in positions.items() if key in states}
    if not positions:
        return None
    return {'positions': positions, 'state_ids': [states[key] for key in positions],
            'source': {'file': SOURCE, 'record_id': record_id, 'row': row_index,
                       'release_id': release_id, 'raw_path': '$.Data'}}
