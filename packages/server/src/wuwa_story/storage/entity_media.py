"""Resolve published entity artwork in one bounded catalog query."""

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference


async def entity_image_urls(session, owner_ids: list[int]) -> dict[int, str]:
    if not owner_ids:
        return {}
    rows = await session.execute(
        select(FileReference, FileObject)
        .join(FileObject, FileObject.id == FileReference.file_id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            FileReference.owner_node_id.in_(owner_ids),
            FileReference.reference_type == "entity_image",
            FileLocation.available.is_(True),
            FileLocation.is_primary.is_(True),
            FileObject.mime_type.in_(["image/png", "image/jpeg", "image/webp", "image/avif"]),
        )
        .order_by(FileReference.id.desc())
    )
    best = {}
    for reference, file in rows:
        path = reference.source_path or ""
        rank = 0 if "IconRoleHead256/" in path else 1 if "IconRoleHead150/" in path else 2
        if reference.owner_node_id not in best or rank < best[reference.owner_node_id][0]:
            best[reference.owner_node_id] = (rank, file.id)
    return {owner: f"/api/media/files/{file_id}" for owner, (_, file_id) in best.items()}
