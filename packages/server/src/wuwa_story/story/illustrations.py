"""Resolve dialogue backgrounds once per action; never attach an unrelated picture."""

from sqlalchemy import select

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.ingestion.talk_backgrounds import background_changes
from wuwa_story.storage.s3 import S3Storage


async def illustration_payloads(session, rows):
    state_ids = {row[2].node_id for row in rows if row[2]}
    if not state_ids:
        return {}
    actions = await repository.illustration_actions(session, state_ids)
    changes = {}
    paths = set()
    spine_paths = set()
    for action_id, _state_id, items in actions:
        for index, background in background_changes(items):
            changes[(action_id, index)] = background
            paths.update(image['engine_path']
                         for image in background['images'])
            if background['kind'] == 'spine':
                spine_paths.add(
                    f'spine_background:{background["resource_id"]}')
    settings = get_settings()
    urls = {}
    bundles = {}
    if paths:
        storage = S3Storage(settings)
        files = await session.execute(select(FileReference.source_path, FileLocation.object_key)
                                      .join(FileObject, FileObject.id == FileReference.file_id)
                                      .join(FileLocation, FileLocation.file_id == FileObject.id)
                                      .where(FileReference.source_path.in_(paths), FileReference.reference_type == 'entity_image',
                                             FileObject.mime_type.in_(
                                          ['image/png', 'image/webp', 'image/jpeg', 'image/avif']),
            FileLocation.backend == 's3', FileLocation.bucket == settings.s3_bucket,
            FileLocation.available.is_(True), FileLocation.is_primary.is_(True))
            .order_by(FileReference.id.desc()))
        for path, key in files:
            urls.setdefault(path, storage.public_url(key))
    if spine_paths:
        storage = S3Storage(settings)
        files = await session.execute(select(FileReference.source_path, FileLocation.object_key,
                                             FileReference.metadata_json, FileObject.id, FileObject.size_bytes)
                                      .join(FileObject, FileObject.id == FileReference.file_id)
                                      .join(FileLocation, FileLocation.file_id == FileObject.id)
                                      .where(FileReference.source_path.in_(spine_paths), FileReference.reference_type == 'story_spine',
                                             FileObject.mime_type == 'application/zip', FileLocation.backend == 's3',
                                             FileLocation.bucket == settings.s3_bucket, FileLocation.available.is_(
                                          True),
            FileLocation.is_primary.is_(True)).order_by(FileReference.id.desc()))
        for path, key, metadata, identity, size in files:
            bundles.setdefault(path, {'url': storage.public_url(key), 'file_id': identity,
                                      'size_bytes': size, 'asset_version': (metadata or {}).get('asset_version')})
    active = {}
    payloads = {}
    for action_id, state_id, items in actions:
        for index in range(len(items or [])):
            if (action_id, index) in changes:
                background = changes[(action_id, index)]
                active[state_id] = {**background, 'id': f'{action_id}:{index}',
                                    'images': [{**image, 'url': urls.get(image['engine_path'])} for image in background['images']]}
                if background['kind'] == 'spine':
                    active[state_id]['bundle'] = bundles.get(
                        f'spine_background:{background["resource_id"]}')
            if state_id in active:
                payloads[(action_id, index)] = active[state_id]
    return {row[0].node_id: payloads[(row[1].node_id, row[0].source_index)] for row in rows
            if row[1] and (row[1].node_id, row[0].source_index) in payloads}
