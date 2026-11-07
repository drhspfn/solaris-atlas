#!/usr/bin/env python3
"""Bounded audited story-sequence backfill. Run in the deployed worker container.

Example, dry-run first (mount/copy this script and the reviewed audit into the container):
  python scripts/backfill-illustrated-sequences.py --audit /data/composition-audit.json \
    --release-id 21 --workspace-id 17391 --limit 1 --entity 'flow_state:...' \
    --report /data/report.json --checkpoint /data/checkpoint.json

Repeat with --apply only after deploying the revision-5 renderer. Use one shared,
durable checkpoint for all invocations; an interrupted dispatch requires review.
"""

import argparse
import asyncio
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select, text
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import GameRelease, ProcessingRun, Processor
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.entity_media import (
    SEQUENCE_METADATA_REVISION,
    MediaRequest,
    entity_media_targets,
)
from wuwa_story.ingestion.media_jobs import enqueue_entity_media
from wuwa_story.ingestion.media_workspaces import workspace_expired
from wuwa_story.storage.s3 import S3Storage

ACTIVE = {'pending', 'queued', 'running', 'waiting_dependency'}
EXPECTED_REVISION = 5


def audit_scope(audit, release_id, entity=None):
    grouped = defaultdict(list)
    for row in audit['references']:
        if (row.get('release_id') == release_id and row.get('composition_reason') is True
                and isinstance(row.get('owner'), str) and row['owner'].startswith('flow_state:')):
            grouped[row['owner']].append(row)
    if entity is not None:
        if entity not in grouped:
            raise ValueError('Entity is not in the audited composition scope')
        return {entity: grouped[entity]}
    return dict(sorted(grouped.items()))


def check_sources(rows, refs, client, targets, extraction_tools=None):
    """Validate audited identities; return source mode and retained bundle IDs."""
    by_id = {r.id: r for r in refs}
    retained = []
    full_source_required = False
    for row in rows:
        ref = by_id.get(row['reference_id'])
        if (ref is None or ref.release_id != row['release_id'] or ref.owner_node_id != row['owner_node_id']
                or ref.source_path != row['source_path'] or ref.file_id != row['file_id']
                or ref.reference_type != row['reference_type']):
            return 'audit_reference_changed', None, []
        source = (ref.metadata_json or {}).get('game_source') or {}
        if any(source.get(k) != client.get(k) for k in ('download_id', 'asset_version', 'tier')):
            return 'workspace_source_mismatch', None, []
        matching = [t for t in targets if t.get('owner') == ref.owner_node_id
                    and t.get('path') == ref.source_path and t.get('reference_type') == ref.reference_type]
        if len(matching) != 1:
            return 'audited_target_not_in_current_resolver', None, []
        target = matching[0]
        if target.get('sequence_metadata_revision') != EXPECTED_REVISION:
            return 'target_has_no_revision_5_invalidation', None, []
        presentation = target.get('presentation_sha256')
        if (not isinstance(presentation, str) or len(presentation) != 64
                or source.get('presentation_sha256') not in (None, presentation)):
            return 'presentation_source_mismatch', None, []
        revision = source.get('sequence_metadata_revision') or 0
        if not isinstance(revision, int) or revision < 0 or revision >= EXPECTED_REVISION:
            return 'source_revision_not_eligible', None, []
        if revision < 4:
            full_source_required = True
            continue
        files = source.get('source_files') or []
        if (not files or not row.get('bundle_available') or any(
                not isinstance(f, dict) or not f.get('package_path')
                or not isinstance(f.get('sha256'), str) or len(f['sha256']) != 64 for f in files)):
            return 'source_bundle_unavailable', None, []
        if (source.get('presentation_sha256') != presentation
                or source.get('spine_recipe') != target.get('spine_recipe')):
            return 'retained_source_not_revision_4_match', None, []
        if source.get('control_sources_complete') is not True:
            return 'retained_control_sources_incomplete', None, []
        if extraction_tools is not None and any(
                (source.get('tools') or {}).get(k) != v for k, v in extraction_tools.items()):
            return 'extraction_tool_mismatch', None, []
        retained.append(ref.id)
    return None, 'full_source_required' if full_source_required else 'retained_revision_4', retained


def payload_hash(request, targets):
    payload = {'schema_version': 1, 'job_type': 'media.entities',
               'request': request.model_dump(), 'targets': targets}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()


def existing_run_action(active, same):
    if active:
        return 'hold_active_revision', active[0]
    if same:
        return 'hold_existing_run', same
    return None, None


def select_pending(scope, entries, limit):
    remaining = [owner for owner in scope if owner not in entries]
    return remaining[:limit], len(remaining) - min(len(remaining), limit)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False,
                   indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


async def main(args):
    audit_bytes = args.audit.read_bytes()
    scope = audit_scope(json.loads(audit_bytes), args.release_id, args.entity)
    identity = {'audit_sha256': hashlib.sha256(audit_bytes).hexdigest(), 'release_id': args.release_id,
                'workspace_id': args.workspace_id, 'revision': EXPECTED_REVISION}
    checkpoint = json.loads(args.checkpoint.read_text(encoding='utf-8')) if args.checkpoint.exists() else {
        'identity': identity, 'entries': {}}
    if checkpoint.get('identity') != identity:
        raise ValueError(
            'Checkpoint audit/release/workspace/revision differs; use a separate checkpoint')
    entries = checkpoint['entries']
    selected, remaining = select_pending(scope, entries, args.limit)
    report = {'identity': identity, 'apply': args.apply, 'audited_owners': len(scope),
              'selected': len(selected), 'remaining_after_selection': remaining,
              'checkpointed': len(entries), 'results': []}
    storage = S3Storage(get_settings())
    async with SessionFactory() as session:
        if not args.apply:
            await session.execute(text('SET TRANSACTION READ ONLY'))
        prepare = await session.get(ProcessingRun, args.workspace_id)
        release = await session.get(GameRelease, args.release_id)
        if (release is None or prepare is None or prepare.status != 'completed'
                or (prepare.metadata_json or {}).get('request', {}).get('kind') != 'prepare'
                or workspace_expired(prepare)):
            raise ValueError(
                'Release or retained completed workspace is unavailable')
        client = prepare.raw_output or {}
        if client.get('workspace_id') != args.workspace_id:
            raise ValueError('Workspace identity differs from preparation')
        root = Path(os.getenv('WUWA_ASSET_WORKSPACE', '/var/lib/wuwa-worker/client-assets')) / 'patch-workspaces' / str(prepare.id)
        plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
        status = json.loads((root / 'status.json').read_text(encoding='utf-8'))
        if (plan.get('id') != client.get('download_id') or plan.get('version') != client.get('asset_version')
                or plan.get('tier') != client.get('tier') or status.get('id') != plan.get('id')
                or status.get('state') != 'downloaded' or not (root / 'game').is_dir()
                or not (root / 'keys.txt').is_file()):
            raise ValueError('Retained workspace files do not match its prepared client')
        from wuwa_story_worker.tooling import tool_path
        extraction_tools = {}
        for name in ('WUWA_FMODEL_PATH', 'WUWA_TEXTURE_CONVERTER_PATH'):
            with tool_path(name).open('rb') as stream:
                extraction_tools[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        report['workspace'] = {k: client.get(k) for k in (
            'asset_version', 'tier', 'download_id')}
        report['deployed_sequence_revision'] = SEQUENCE_METADATA_REVISION
        if args.apply and SEQUENCE_METADATA_REVISION != EXPECTED_REVISION:
            raise ValueError('Revision 5 is not deployed; refusing to checkpoint or enqueue')
        for owner in selected:
            result = {'owner': owner, 'audit_reference_ids': [
                r['reference_id'] for r in scope[owner]]}
            try:
                if SEQUENCE_METADATA_REVISION != EXPECTED_REVISION:
                    result['action'] = 'hold_revision_not_deployed'
                else:
                    request = MediaRequest(download_id=client['download_id'], asset_version=client['asset_version'],
                                           tier=client['tier'], workspace_id=prepare.id, release_id=release.id,
                                           game_version=release.game_version, entities=[
                        owner], kinds=['image'],
                        voice_plan_id=client.get('voice_plan_id'))
                    targets = await entity_media_targets(session, request)
                    refs = list(await session.scalars(select(FileReference).where(
                        FileReference.id.in_(result['audit_reference_ids']))))
                    reason, source_mode, retained_ids = check_sources(scope[owner], refs, client, targets, extraction_tools)
                    result['source_mode'] = source_mode
                    if not reason:
                        result['retained_reference_ids'] = retained_ids
                        result['full_source_reference_ids'] = [ref.id for ref in refs if ref.id not in retained_ids]
                        for ref in (ref for ref in refs if ref.id in retained_ids):
                            row = (await session.execute(select(FileObject.size_bytes, FileLocation.object_key)
                                .join(FileLocation, FileLocation.file_id == FileObject.id)
                                .where(FileObject.id == ref.file_id, FileLocation.backend == storage.backend,
                                       FileLocation.bucket == storage.bucket, FileLocation.is_primary.is_(True),
                                       FileLocation.available.is_(True)))).first()
                            stat = await storage.stat(row.object_key) if row else None
                            if row is None or stat is None or stat.size_bytes != row.size_bytes:
                                reason = 'source_bundle_missing_or_size_mismatch'
                                break
                    if reason:
                        result['action'] = 'hold_' + reason
                    else:
                        digest = payload_hash(request, targets)
                        result['input_hash'] = digest.hex()
                        runs = list(await session.scalars(select(ProcessingRun).join(Processor).where(
                            Processor.key == 'entity_media',
                            ProcessingRun.metadata_json['request']['release_id'].as_integer(
                            ) == release.id,
                            ProcessingRun.metadata_json['request']['entities'].contains([
                                                                                        owner]),
                            ProcessingRun.metadata_json['payload']['targets'].contains(
                                [{'sequence_metadata_revision': EXPECTED_REVISION}]),
                            ProcessingRun.status.in_(list(ACTIVE))).order_by(ProcessingRun.id.desc()).limit(20)))
                        same = await session.scalar(select(ProcessingRun).join(Processor).where(
                            Processor.key == 'entity_media', ProcessingRun.input_hash == digest
                        ).order_by(ProcessingRun.id.desc()).limit(1))
                        action, prior = existing_run_action(runs, same)
                        if prior:
                            result.update(action=action, run_id=prior.id, status=prior.status)
                        elif args.apply:
                            entries[owner] = {
                                'state': 'dispatching', 'input_hash': digest.hex()}
                            write_json(args.checkpoint, checkpoint)
                            run = await enqueue_entity_media(session, request, retry_partial=False, republish_queued=False)
                            result.update(action='enqueued_or_existing',
                                          run_id=run.id, status=run.status)
                            entries[owner] = {
                                'state': 'submitted', 'run_id': run.id, 'input_hash': digest.hex()}
                            write_json(args.checkpoint, checkpoint)
                        else:
                            result['action'] = 'would_enqueue'
            except Exception as error:  # noqa: BLE001 - retain uncertain dispatches for manual review.
                await session.rollback()
                result.update(action='hold_error', error_type=type(
                    error).__name__, error=str(error)[:300])
                # dispatching persists after an uncertain publish; never blindly retry.
            if args.apply and owner not in entries:
                entries[owner] = {'state': 'held', 'action': result['action']}
                write_json(args.checkpoint, checkpoint)
            report['results'].append(result)
            write_json(args.report, report)
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False))


async def run(args):
    try:
        if args.apply:
            # Serialize this coordinator per release even with different checkpoints.
            async with engine.connect() as lock:
                acquired = await lock.scalar(text('SELECT pg_try_advisory_lock(73142, :release)'),
                                             {'release': args.release_id})
                if not acquired:
                    raise ValueError('Another illustrated backfill owns this release')
                try:
                    await main(args)
                finally:
                    await lock.execute(text('SELECT pg_advisory_unlock(73142, :release)'),
                                       {'release': args.release_id})
        else:
            await main(args)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    import fcntl  # Container execution only; importing helpers also works on Windows.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--release-id', type=int, required=True)
    parser.add_argument('--workspace-id', type=int, required=True)
    parser.add_argument('--limit', type=int, default=1,
                        choices=range(1, 51), metavar='1..50')
    parser.add_argument('--entity')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    if not 0 < options.release_id < 2**31 or options.workspace_id < 1:
        parser.error('release-id and workspace-id must be positive')
    if len({options.report.resolve(), options.audit.resolve(), options.checkpoint.resolve()}) != 3:
        parser.error('report, audit and checkpoint must be different files')
    options.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with options.checkpoint.with_name(options.checkpoint.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(options))
