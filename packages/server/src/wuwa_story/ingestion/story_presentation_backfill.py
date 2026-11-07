"""Queue source-backed story illustrations against an explicitly retained client.

Dry-run by default. One state per job isolates unsupported compositions. Reusing
the request reuses its run identity; --republish-queued explicitly recovers lost
broker messages. Existing exact publications are checked by the normal worker.
"""

import argparse
import asyncio
import json

from sqlalchemy import select

from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.db.models.ops import GameRelease, ProcessingRun
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.entity_media import MediaRequest, flow_background_paths
from wuwa_story.ingestion.media_jobs import enqueue_entity_media
from wuwa_story.ingestion.media_workspaces import workspace_expired
from wuwa_story.ingestion.spine_presentation import flow_presentation


async def candidates(session, release_id):
    rows = await session.execute(select(Node.canonical_key, SourceRecord.data)
        .join(NodeRevision, NodeRevision.node_id == Node.id)
        .join(SourceRecord, SourceRecord.id == NodeRevision.source_record_id)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(NodeRevision.release_id == release_id, SourceRecord.release_id == release_id,
               SourceFile.release_id == release_id,
               SourceFile.logical_source_path == 'BinData/flowState/flowstate.json',
               (SourceRecord.data['Actions'].as_string().contains('SequenceDataAsset')
                | SourceRecord.data['Actions'].as_string().contains('PlaySequenceData')
                | SourceRecord.data['Actions'].as_string().contains('BackgroundConfig')),
               Node.canonical_key.startswith('flow_state:')))
    selected = set()
    for key, raw in rows:
        presentation = flow_presentation(raw)
        if (flow_background_paths(raw) or presentation['resource_ids']
                or any(cue['kind'] == 'sequence' for cue in presentation['cues'])):
            selected.add(key)
    return sorted(selected)


async def main(args):
    try:
        async with SessionFactory() as session:
            release = await session.get(GameRelease, args.release_id)
            parent = await session.get(ProcessingRun, args.workspace_id)
            if (release is None or parent is None or parent.status != 'completed'
                    or (parent.metadata_json or {}).get('request', {}).get('kind') != 'prepare'
                    or workspace_expired(parent)):
                raise ValueError('Select an imported release and a completed retained client preparation')
            client = parent.raw_output or {}
            if client.get('workspace_id') != parent.id:
                raise ValueError('Preparation does not retain its own workspace')
            keys = await candidates(session, release.id)
            if args.entity:
                if not set(args.entity) <= set(keys):
                    raise ValueError('Selected states have no authored presentation in this release')
                keys = sorted(set(args.entity))
            # Source-selected priorities remain explicit, not inferred from names.
            if not set(args.priority_entity) <= set(keys):
                raise ValueError('Priority state is outside the selected source scope')
            keys.sort(key=lambda key: (key not in args.priority_entity, key))
            print(json.dumps({'release_id': release.id, 'states': len(keys), 'apply': args.apply}), flush=True)
            failed = []
            for key in keys:
                request = MediaRequest(download_id=client['download_id'], asset_version=client['asset_version'],
                    tier=client['tier'], workspace_id=parent.id, release_id=release.id,
                    game_version=release.game_version, entities=[key], kinds=['image'],
                    voice_plan_id=client.get('voice_plan_id'))
                if args.apply:
                    try:
                        run = await enqueue_entity_media(session, request, retry_partial=True,
                                                        republish_queued=args.republish_queued)
                        print(json.dumps({'entity': key, 'run_id': run.id, 'status': run.status}, ensure_ascii=True), flush=True)
                    except Exception as error:
                        await session.rollback()
                        failed.append(key)
                        print(json.dumps({'entity': key, 'status': 'enqueue_failed',
                                          'error_type': type(error).__name__}, ensure_ascii=True), flush=True)
            if failed:
                raise RuntimeError(f'{len(failed)} states could not be queued; retry the reported exact entities')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', type=int, required=True)
    parser.add_argument('--workspace-id', type=int, required=True)
    parser.add_argument('--entity', action='append', default=[])
    parser.add_argument('--priority-entity', action='append', default=[])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--republish-queued', action='store_true')
    options = parser.parse_args()
    if options.republish_queued and not options.apply:
        parser.error('--republish-queued requires --apply')
    asyncio.run(main(options))
