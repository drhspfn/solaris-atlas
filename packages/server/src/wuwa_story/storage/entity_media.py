"""Resolve published entity artwork in one bounded catalog query."""

from sqlalchemy import select

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.storage.s3 import S3Storage


async def entity_image_urls(session, owner_ids: list[int]) -> dict[int, str]:
    if not owner_ids:
        return {}
    settings = get_settings()
    storage = S3Storage(settings)
    rows = await session.execute(
        select(FileReference, FileObject, FileLocation.object_key)
        .join(FileObject, FileObject.id == FileReference.file_id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            FileReference.owner_node_id.in_(owner_ids),
            FileReference.reference_type == "entity_image",
            FileLocation.available.is_(True),
            FileLocation.is_primary.is_(True),
            FileLocation.backend == "s3",
            FileLocation.bucket == settings.s3_bucket,
            FileObject.mime_type.in_(["image/png", "image/jpeg", "image/webp", "image/avif"]),
        )
        .order_by(FileReference.id.desc())
    )
    best = {}
    for reference, _file, object_key in rows:
        path = reference.source_path or ""
        rank = 0 if "IconRoleHead256/" in path else 1 if "IconRoleHead150/" in path else 2
        if reference.owner_node_id not in best or rank < best[reference.owner_node_id][0]:
            best[reference.owner_node_id] = (rank, storage.public_url(object_key))
    return {owner: url for owner, (_, url) in best.items()}
