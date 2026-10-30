"""Resolve published entity artwork in one bounded catalog query."""

from sqlalchemy import select

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.ingestion.character_artwork import character_artwork_scope
from wuwa_story.storage.s3 import S3Storage


async def entity_image_urls(session, owner_ids: list[int]) -> dict[int, str]:
    if not owner_ids:
        return {}
    settings = get_settings()
    storage = S3Storage(settings)
    # Resolve the page's references before touching the much larger media tables.
    # Otherwise PostgreSQL can start with every published image in the archive.
    candidates = (
        select(FileReference.id, FileReference.owner_node_id, FileReference.file_id,
               FileReference.source_path)
        .where(FileReference.owner_node_id.in_(owner_ids),
               FileReference.reference_type == "entity_image")
        .cte("entity_image_candidates")
        .prefix_with("MATERIALIZED")
    )
    rows = await session.execute(
        select(candidates.c.owner_node_id, candidates.c.source_path,
               FileLocation.object_key, Node.canonical_key)
        .select_from(candidates)
        .join(Node, Node.id == candidates.c.owner_node_id)
        .join(FileObject, FileObject.id == candidates.c.file_id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            FileLocation.available.is_(True),
            FileLocation.is_primary.is_(True),
            FileLocation.backend == "s3",
            FileLocation.bucket == settings.s3_bucket,
            FileObject.mime_type.in_(["image/png", "image/jpeg", "image/webp", "image/avif"]),
        )
        .order_by(candidates.c.id.desc())
    )
    best = {}
    for owner_id, source_path, object_key, canonical_key in rows:
        path = source_path or ""
        role_key = canonical_key.partition(":")[2]
        role_id = int(role_key) if role_key.isdecimal() else None
        if canonical_key.startswith("character:") and any(
            character_artwork_scope(field, path, role_id) == "shared_resource"
            for field in ("RoleStand", "RolePortrait", "Icon")
        ):
            continue
        rank = 0 if "IconRoleHead256/" in path else 1 if "IconRoleHead150/" in path else 2
        if owner_id not in best or rank < best[owner_id][0]:
            best[owner_id] = (rank, storage.public_url(object_key))
    return {owner: url for owner, (_, url) in best.items()}
