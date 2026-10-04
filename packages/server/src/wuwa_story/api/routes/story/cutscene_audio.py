"""Resolve complete audio bundles for the exact video build."""

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileReference


async def audio_bundles(session, owner_ids, version, settings, storage):
    if not owner_ids:
        return {}
    references = await session.scalars(
        select(FileReference)
        .where(
            FileReference.owner_node_id.in_(owner_ids),
            FileReference.reference_type == "cutscene_audio_bundle",
            FileReference.metadata_json["asset_version"].astext == version,
        )
        .order_by(FileReference.id.desc())
    )
    selected = {}
    for reference in references:
        selected.setdefault(reference.owner_node_id, reference.metadata_json)
    ids = {track["file_id"] for data in selected.values() for track in data["tracks"]}
    if not ids:
        return {}
    locations = dict(
        (
            await session.execute(
                select(FileLocation.file_id, FileLocation.object_key).where(
                    FileLocation.file_id.in_(ids),
                    FileLocation.available.is_(True),
                    FileLocation.is_primary.is_(True),
                    FileLocation.backend == "s3",
                    FileLocation.bucket == settings.s3_bucket,
                )
            )
        ).all()
    )
    result = {}
    for owner, data in selected.items():
        # An incomplete bundle must never silently lose voices or effects.
        if all(track["file_id"] in locations for track in data["tracks"]):
            result[owner] = [
                {
                    "role": track["role"],
                    "language": track["language"],
                    "url": storage.public_url(locations[track["file_id"]]),
                }
                for track in data["tracks"]
            ]
    return result
