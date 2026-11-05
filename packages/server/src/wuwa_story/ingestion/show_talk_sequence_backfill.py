"""Repair ShowTalk sequence references from imported snapshot rows, without recompiling.

Dry-run by default. Select a release and optional exact state keys; --max-links
bounds reads and writes. No media jobs or projection refreshes are enqueued.
"""

import argparse
import asyncio
import hashlib
import json
from types import SimpleNamespace

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision, NodeType
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.dungeon_context import decoded, walk
from wuwa_story.ingestion.runtime_media import runtime_media

SOURCE = 'BinData/flowState/flowstate.json'
RELATION = 'plays_sequence_asset'


def sequence_links(rows):
    for source_id, index, row in rows:
        state = row.get('StateKey')
        actions = decoded(row.get('Actions'))
        if not isinstance(state, str) or not state or not isinstance(actions, list):
            continue
        for action_index, action in enumerate(actions):
            action_path = f'$[{index}].Actions<json>[{action_index}]'
            for path, value in walk(action, action_path):
                if value.get('Name') != 'ShowTalk':
                    continue
                owner = (f'action:{state}:{action_index}' if path == action_path
                         else f'nested_action:{state}:{path}')
                for ref in runtime_media(value, path, recursive=False):
                    if ref['kind'] == 'sequence':
                        yield {'from': owner, 'to': ref['reference'], 'type': RELATION,
                               'relation': 'explicit_reference', 'source': SOURCE,
                               'raw_path': ref['raw_path'], 'basis': ref['basis'],
                               'state_key': state, 'source_record_id': source_id, 'row': index}


async def backfill(session, release_id, *, state_keys=(), apply=False, max_links=1000):
    if type(release_id) is not int or release_id <= 0:
        raise ValueError('A positive imported release ID is required')
    if type(max_links) is not int or not 1 <= max_links <= 10000:
        raise ValueError('max_links must be between 1 and 10000')
    if any(not isinstance(key, str) or not key for key in state_keys):
        raise ValueError('State keys must be nonempty exact source keys')
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
    if apply:
        await session.execute(text('SELECT pg_advisory_xact_lock(707, :id)'), {'id': release_id})
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError('Unknown imported release')
    # Use snapshot raw params, not the mutable latest core.quest_action.params.
    query = select(SourceRecord.id, SourceRecord.row_index, SourceRecord.data).join(
        SourceFile, SourceFile.id == SourceRecord.source_file_id).where(
        SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
        SourceFile.logical_source_path == SOURCE,
        SourceRecord.data['Actions'].as_string().contains('"SequenceDataAsset"'))
    if state_keys:
        query = query.where(SourceRecord.data['StateKey'].as_string().in_(sorted(set(state_keys))))
    rows = list(await session.execute(query.order_by(SourceRecord.row_index).limit(max_links + 1)))
    candidates = list(sequence_links(rows))
    if len(rows) > max_links or len(candidates) > max_links:
        raise ValueError('Sequence repair would exceed max_links; narrow state keys or raise the explicit bound')
    owners = {key: node_id for key, node_id in await session.execute(select(Node.canonical_key, Node.id).join(
        NodeRevision, NodeRevision.node_id == Node.id).where(
        NodeRevision.release_id == release_id,
        Node.canonical_key.in_({link['from'] for link in candidates})).distinct())} if candidates else {}
    links = [link for link in candidates if link['from'] in owners]
    targets = {link['to']: link for link in links}
    existing_assets = {key: node_id for key, node_id in await session.execute(select(Node.canonical_key, Node.id).where(
        Node.canonical_key.in_(targets)))} if targets else {}
    revised = set(await session.scalars(select(NodeRevision.node_id).where(
        NodeRevision.release_id == release_id, NodeRevision.node_id.in_(existing_assets.values())))) if existing_assets else set()
    existing = set(await session.execute(select(Node.canonical_key, Edge.to_node_id, EdgeEvidence.source_raw_path)
        .select_from(Edge).join(Node, Node.id == Edge.from_node_id)
        .join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id).where(
            Edge.from_node_id.in_(owners.values()), RelationType.key == RELATION,
            Edge.layer == 'source', Edge.basis == 'explicit_reference',
            EdgeEvidence.release_id == release_id, EdgeEvidence.source_file_path == SOURCE))) if links else set()
    missing = [link for link in links if (link['from'], existing_assets.get(link['to']), link['raw_path']) not in existing]
    missing_revisions = [key for key in targets if existing_assets.get(key) not in revised]
    report = {'release_id': release_id, 'game_version': release.game_version, 'apply': apply,
              'source_rows': len(rows), 'candidate_links': len(candidates),
              'uncompiled_owners': len(candidates) - len(links), 'missing_links': len(missing),
              'missing_asset_nodes': len(set(targets) - existing_assets.keys()),
              'missing_asset_revisions': len(missing_revisions),
              'state_keys': sorted({link['state_key'] for link in missing}), 'written': 0}
    if not apply or not (missing or missing_revisions):
        return report
    asset_type = await session.scalar(select(NodeType.id).where(NodeType.key == 'asset_reference'))
    if asset_type is None:
        raise ValueError('asset_reference node type is not seeded')
    if targets:
        await session.execute(insert(Node.__table__).values([
            {'canonical_key': key, 'type_id': asset_type, 'created_release_id': release_id,
             'metadata': {'source_file': SOURCE, 'source_row': link['row'], 'source_raw_path': link['raw_path']}}
            for key, link in sorted(targets.items())
        ]).on_conflict_do_nothing(index_elements=[Node.canonical_key]))
        # Lock only these assets: concurrent release repairs must not allocate the
        # same per-node revision number. Existing node metadata stays untouched.
        assets = list(await session.execute(select(Node.canonical_key, Node.id, Node.type_id).where(
            Node.canonical_key.in_(targets)).order_by(Node.canonical_key).with_for_update()))
        if any(kind != asset_type for _, _, kind in assets):
            raise ValueError('Sequence asset key has an incompatible graph type')
        ids = {key: node_id for key, node_id, _ in assets}
        revised = set(await session.scalars(select(NodeRevision.node_id).where(
            NodeRevision.release_id == release_id, NodeRevision.node_id.in_(ids.values()))))
        versions = {node_id: revision for node_id, revision in await session.execute(
            select(NodeRevision.node_id, func.max(NodeRevision.revision))
            .where(NodeRevision.node_id.in_(ids.values())).group_by(NodeRevision.node_id))}
        values = [{'node_id': ids[key], 'release_id': release_id,
                   'revision': versions.get(ids[key], 0) + 1, 'source_record_id': link['source_record_id'],
                   'content_hash': hashlib.sha256(key.encode()).digest(),
                   'metadata': {'source_file': SOURCE, 'source_row': link['row'], 'source_raw_path': link['raw_path']}}
                  for key, link in sorted(targets.items()) if ids[key] not in revised]
        if values:
            await session.execute(insert(NodeRevision.__table__).values(values))
    records = [{**link, 'version': release.game_version, 'source_repository': release.upstream_name,
                'source_commit': release.upstream_commit} for link in missing]
    if records:
        # One importer batch commits nodes, revisions and evidence atomically.
        await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(records)), session,
                                     release_id, batch_size=max_links)
    else:
        await session.commit()
    report['written'] = len(records)
    return report


async def main(args):
    async with SessionFactory() as session:
        print(json.dumps(await backfill(session, args.release_id, state_keys=args.state_key,
                                        apply=args.apply, max_links=args.max_links), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', type=int, required=True)
    parser.add_argument('--state-key', action='append', default=[])
    parser.add_argument('--max-links', type=int, default=1000)
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args()))
