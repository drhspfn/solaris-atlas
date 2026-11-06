"""Plan a fresh normal request for one prior, explicitly scoped sequence job."""

import hashlib
import json

from sqlalchemy import select
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.ingestion.entity_media import MediaRequest, entity_media_targets
from wuwa_story.ingestion.media_jobs import enqueue_entity_media


async def enqueue_metadata_revision(session, run_id, *, apply=False):
    original = await session.get(ProcessingRun, run_id)
    if original is None:
        raise ValueError('Source sequence run does not exist')
    processor = await session.get(Processor, original.processor_id)
    if processor is None or processor.key != 'entity_media':
        raise ValueError('Select a normal entity-media sequence child')
    payload = (original.metadata_json or {}).get('payload') or {}
    request = MediaRequest.model_validate(payload.get('request'))
    old = payload.get('targets') or []
    canonical = {key: value for key, value in payload.items() if key != 'run_id'}
    if (payload.get('run_id') != run_id or not original.input_hash
            or hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).digest() != original.input_hash
            or not old or len(old) > 100 or request.kinds != ['image']
            or any(not target.get('path', '').startswith('story_sequence:') for target in old)):
        raise ValueError('Select a pinned sequence-only source run, not a broad image import')
    current = await entity_media_targets(session, request)
    previous = {target['path']: target for target in old}
    if len(current) != len(old) or any(target['path'] not in previous or any(
            target.get(key) != previous[target['path']].get(key)
            for key in ('owner', 'release_id', 'presentation_sha256', 'spine_recipe', 'sequence_media_schema'))
            for target in current):
        raise ValueError('Authored sequence source changed; review a normal source recovery instead')
    canonical = {'schema_version': 1, 'job_type': 'media.entities',
                 'request': request.model_dump(), 'targets': current}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).digest()
    found = await session.scalar(select(ProcessingRun).where(
        ProcessingRun.processor_id == original.processor_id, ProcessingRun.input_hash == digest,
    ).order_by(ProcessingRun.id.desc()).limit(1))
    report = {'source_run_id': run_id, 'source_status': original.status,
              'current_input_hash': digest.hex(), 'target_count': len(current),
              'revisions': [{key: target.get(key) for key in ('owner', 'sequence_metadata_revision')}
                            for target in current], 'applied': False}
    if original.status in {'queued', 'pending', 'running', 'waiting_dependency'}:
        return {**report, 'action': 'wait_source_terminal'}
    if original.status == 'cancelled':
        return {**report, 'action': 'hold_cancelled'}
    if found and found.status in {'queued', 'running', 'waiting_dependency', 'completed', 'partial'}:
        return {**report, 'action': 'reuse_current_revision', 'run_id': found.id, 'status': found.status}
    if apply:
        run = await enqueue_entity_media(session, request)
        return {**report, 'applied': True, 'action': 'normal_enqueue', 'run_id': run.id, 'status': run.status}
    return {**report, 'action': 'normal_enqueue_after_review'}
