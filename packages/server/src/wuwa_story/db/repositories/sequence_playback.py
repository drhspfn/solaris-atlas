"""Read published sequence fragments for exact flow/release owners in two batches."""

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference


async def bundles(session, owners, release_id):
    if not owners:
        return []
    return list(await session.execute(select(
        FileReference.owner_node_id, FileReference.metadata_json,
    ).where(
        FileReference.owner_node_id.in_(owners),
        FileReference.release_id == release_id,
        FileReference.reference_type.in_(['story_presentation', 'story_spine']),
    ).order_by(FileReference.id.desc())))


async def available_movies(session, identities, bucket):
    if not identities:
        return {}
    return dict(await session.execute(select(FileObject.id, FileLocation.object_key).join(
        FileLocation, FileLocation.file_id == FileObject.id,
    ).where(
        FileObject.id.in_(identities), FileObject.mime_type == 'video/mp4',
        FileLocation.backend == 's3', FileLocation.bucket == bucket,
        FileLocation.available.is_(True), FileLocation.is_primary.is_(True),
    )))
