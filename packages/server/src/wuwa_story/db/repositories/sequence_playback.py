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
        FileReference.reference_type.in_(
            ['story_presentation', 'story_spine']),
    ).order_by(FileReference.id.desc())))


async def available_movies(session, selected, release_id, bucket):
    identities = {section['file_id'] for bundle in selected.values()
                  for section in bundle['media']['sections']
                  if section.get('kind') == 'movie_fragment' and type(section.get('file_id')) is int}
    if not identities or release_id is None:
        return {}
    rows = await session.execute(select(
        FileReference.owner_node_id, FileObject.id, FileReference.source_path,
        FileReference.source_name, FileReference.metadata_json, FileLocation.object_key,
    ).select_from(FileReference).join(FileObject, FileReference.file_id == FileObject.id).join(
        FileLocation, FileLocation.file_id == FileObject.id,
    ).where(
        FileReference.owner_node_id.in_(
            selected), FileReference.release_id == release_id,
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
            if (section.get('kind') == 'movie_fragment' and section.get('file_id') == file_id and section.get('source') in sources
                    and section.get('file') == name == metadata.get('archive_entry')
                    and section.get('archive_path') == metadata.get('archive_path')):
                result[(owner, file_id, section['source'])] = object_key
    return result


async def available_audio(session, selected, release_id, bucket):
    """Resolve only soundtrack files published with the exact selected scene bundle."""
    identities = {track['file_id'] for bundle in selected.values()
                  for section in bundle['media']['sections'] if section.get('kind') == 'audio_event'
                  for track in section.get('tracks', []) if isinstance(track, dict)
                  and track.get('status') == 'published' and type(track.get('file_id')) is int}
    if not identities or release_id is None:
        return {}
    rows = await session.execute(select(
        FileReference.owner_node_id, FileObject.id, FileReference.source_path,
        FileReference.source_name, FileReference.metadata_json, FileLocation.object_key,
    ).select_from(FileReference).join(FileObject, FileReference.file_id == FileObject.id).join(
        FileLocation, FileLocation.file_id == FileObject.id,
    ).where(
        FileReference.owner_node_id.in_(
            selected), FileReference.release_id == release_id,
        FileReference.reference_type == 'story_sequence_audio', FileObject.id.in_(
            identities),
        FileObject.mime_type == 'audio/ogg', FileLocation.backend == 's3', FileLocation.bucket == bucket,
        FileLocation.available.is_(True), FileLocation.is_primary.is_(True),
    ))
    result = {}
    for owner, file_id, path, name, metadata, key in rows:
        bundle = selected[owner]
        proof = bundle['media']['game_source']
        if (not isinstance(metadata, dict) or path != bundle['source_path']
                or metadata.get('bundle_file_id') != bundle['file_id']
                or metadata.get('game_source') != proof
                or any(metadata.get(field) != proof[field] for field in ('asset_version', 'download_id'))):
            continue
        sources = metadata.get('sections', [metadata.get('section_source')])
        if not isinstance(sources, list):
            continue
        for section in bundle['media']['sections']:
            if (section.get('kind') != 'audio_event' or section.get('source') not in sources
                    or section.get('reference') != metadata.get('event_reference')):
                continue
            for track in section.get('tracks', []):
                if (isinstance(track, dict) and track.get('status') == 'published'
                        and track.get('file_id') == file_id and track.get('file') == name == metadata.get('archive_entry')
                        and track.get('language') == metadata.get('language')
                        and track.get('rover') == metadata.get('rover')):
                    result[(owner, file_id, section['source'])] = key
    return result


async def available_illustrations(session, selected, release_id, bucket):
    """Resolve images only from the selected bundle and its exact frame metadata."""
    identities = {section['file_id'] for bundle in selected.values()
                  for section in bundle['media']['sections']
                  if section.get('kind') == 'illustration' and type(section.get('file_id')) is int}
    if not identities or release_id is None:
        return {}
    rows = await session.execute(select(
        FileReference.owner_node_id, FileObject.id, FileReference.source_path,
        FileReference.source_name, FileReference.metadata_json, FileLocation.object_key,
    ).select_from(FileReference).join(FileObject, FileReference.file_id == FileObject.id).join(
        FileLocation, FileLocation.file_id == FileObject.id,
    ).where(
        FileReference.owner_node_id.in_(
            selected), FileReference.release_id == release_id,
        FileReference.reference_type == 'story_sequence_illustration', FileObject.id.in_(
            identities),
        FileObject.mime_type == 'image/png', FileLocation.backend == 's3', FileLocation.bucket == bucket,
        FileLocation.available.is_(True), FileLocation.is_primary.is_(True),
    ))
    result = {}
    for owner, file_id, path, name, metadata, key in rows:
        bundle = selected[owner]
        proof = bundle['media']['game_source']
        if (not isinstance(metadata, dict) or path != bundle['source_path']
                or metadata.get('bundle_file_id') != bundle['file_id'] or metadata.get('game_source') != proof
                or any(metadata.get(field) != proof[field] for field in ('asset_version', 'download_id'))
                or not isinstance(metadata.get('frames'), list)):
            continue
        for section in bundle['media']['sections']:
            identity = {field: section.get(field) for field in (
                'source', 'scene_source', 'time_seconds')}
            if (section.get('kind') == 'illustration' and section.get('status') == 'published'
                    and section.get('file_id') == file_id and section.get('file') == name == metadata.get('archive_entry')
                    and identity in metadata['frames']):
                result[(owner, file_id, identity['source'],
                        identity['scene_source'], identity['time_seconds'])] = key
    return result
