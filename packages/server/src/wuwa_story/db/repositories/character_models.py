"""Published model bundles; never project private source metadata into public responses."""

from sqlalchemy import func, select

from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.story.domain import version_key

REFERENCE_TYPES = {
    "character_model_preview": "preview",
    "character_model_download": "download",
    "character_model_physics": "physics",
    "character_model_animations": "animations",
    "character_model_facial": "facial",
}


async def model_release(session, game_version=None):
    query = select(GameRelease)
    if game_version is not None:
        query = query.where(GameRelease.game_version == game_version)
    return max(
        await session.scalars(query),
        key=lambda release: (version_key(release.game_version), release.sequence),
        default=None,
    )


async def published_model_releases(session, owner_node_id):
    """Find snapshots with model publications in publication order."""
    bundle = FileReference.metadata_json["model_bundle_id"].astext
    return list(await session.scalars(
        select(GameRelease).join(FileReference, FileReference.release_id == GameRelease.id)
        .where(FileReference.owner_node_id == owner_node_id,
               FileReference.reference_type.in_(("character_model_preview", "character_model_download")),
               bundle.is_not(None))
        .group_by(GameRelease.id, bundle)
        .having(func.bool_or(FileReference.reference_type == "character_model_preview"),
                func.bool_or(FileReference.reference_type == "character_model_download"))
        .order_by(func.max(FileReference.id).desc())
    ))


async def published_model_files(session, owner_node_id, release_id, bucket):
    return list(
        await session.execute(
            select(
                FileReference.reference_type,
                FileReference.metadata_json["model_bundle_id"].astext.label("model_bundle_id"),
                FileLocation.object_key,
                FileObject.size_bytes,
                FileReference.metadata_json["viewer_object_key"].astext.label("viewer_object_key"),
                FileReference.metadata_json["form_id"].label("form_id"),
                FileReference.metadata_json["form_label"].label("form_label"),
                FileReference.metadata_json["form_order"].label("form_order"),
            )
            .join(FileObject, FileObject.id == FileReference.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(
                FileReference.owner_node_id == owner_node_id,
                FileReference.release_id == release_id,
                FileReference.reference_type.in_(REFERENCE_TYPES),
                FileLocation.backend == "s3",
                FileLocation.bucket == bucket,
                FileLocation.available.is_(True),
                FileLocation.is_primary.is_(True),
            )
            .order_by(FileReference.id.desc())
        )
    )
