"""Read published sequence fragments for exact flow/release owners in two batches."""

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference


async def bundles(session, owners, release_id):
    if not owners:
        return []
    return list(await session.execute(select(
        FileReference.owner_node_id, FileReference.file_id,
        FileReference.source_path, FileReference.metadata_json,
    ).where(
        FileReference.owner_node_id.in_(owners),
        FileReference.release_id == release_id,
        FileReference.reference_type.in_(['story_presentation', 'story_spine']),
    ).order_by(FileReference.id.desc())))


async def available_movies(session, selected, release_id, bucket):
    identities = {section['file_id'] for bundle in selected.values()
                  for section in bundle['media']['sections']}
    if not identities or release_id is None:
        return {}
    rows = await session.execute(select(
        FileReference.owner_node_id, FileObject.id, FileReference.source_path,
        FileReference.source_name, FileReference.metadata_json, FileLocation.object_key,
    ).select_from(FileReference).join(FileObject, FileReference.file_id == FileObject.id).join(
        FileLocation, FileLocation.file_id == FileObject.id,
    ).where(
        FileReference.owner_node_id.in_(selected), FileReference.release_id == release_id,
        FileReference.reference_type == 'story_sequence_fragment',
        FileObject.id.in_(identities), FileObject.mime_type == 'video/mp4',
        FileLocation.backend == 's3', FileLocation.bucket == bucket,
        FileLocation.available.is_(True), FileLocation.is_primary.is_(True),
    ))
    result = {}
    for owner, file_id, path, name, metadata, object_key in rows:
        bundle = selected[owner]
        proof = bundle['media']['game_source']
        metadata = metadata if isinstance(metadata, dict) else {}
        if (path != bundle['source_path'] or metadata.get('bundle_file_id') != bundle['file_id']
                or metadata.get('game_source') != proof
                or any(metadata.get(key) != proof[key] for key in ('asset_version', 'download_id'))):
            continue
        sources = metadata.get('sections')
        if not isinstance(sources, list):
            continue
        for section in bundle['media']['sections']:
            if (section['file_id'] == file_id and section.get('source') in sources
                    and section.get('file') == name == metadata.get('archive_entry')
                    and section.get('archive_path') == metadata.get('archive_path')):
                result[(owner, file_id, section['source'])] = object_key
    return result
