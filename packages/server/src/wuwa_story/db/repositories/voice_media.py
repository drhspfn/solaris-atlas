"""Read the small public playback fields without transferring archive metadata."""

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileReference


async def published_voice_tracks(session, owner_ids, bucket):
    if not owner_ids:
        return []
    return list(
        await session.execute(
            select(
                FileReference.owner_node_id,
                FileReference.metadata_json["language"].astext.label("language"),
                FileReference.metadata_json["rover"].astext.label("rover"),
                FileReference.metadata_json["asset_version"].astext.label("asset_version"),
                FileReference.metadata_json["duration_seconds"].label("duration_seconds"),
                FileLocation.object_key,
            )
            .join(FileLocation, FileLocation.file_id == FileReference.file_id)
            .where(
                FileReference.owner_node_id.in_(owner_ids),
                FileReference.reference_type == "voice_audio",
                FileLocation.backend == "s3",
                FileLocation.bucket == bucket,
                FileLocation.available.is_(True),
                FileLocation.is_primary.is_(True),
            )
            .order_by(FileReference.id.desc())
        )
    )


async def published_audio_event_tracks(session, owner_ids, bucket):
    if not owner_ids:
        return []
    return list(
        await session.execute(
            select(
                FileReference.owner_node_id,
                FileReference.metadata_json["language"].astext.label("language"),
                FileReference.metadata_json["rover"].astext.label("rover"),
                FileReference.metadata_json["asset_version"].astext.label("asset_version"),
                FileReference.metadata_json["duration_seconds"].label("duration_seconds"),
                FileLocation.object_key,
            )
            .join(FileLocation, FileLocation.file_id == FileReference.file_id)
            .where(
                FileReference.owner_node_id.in_(owner_ids),
                FileReference.reference_type.in_(("voice_audio", "audio_event", "story_sequence_audio")),
                FileLocation.backend == "s3",
                FileLocation.bucket == bucket,
                FileLocation.available.is_(True),
                FileLocation.is_primary.is_(True),
            )
            .order_by(FileReference.id.desc())
        )
    )


async def published_sequence_audio_tracks(session, owner_ids, release_id, bucket):
    if not owner_ids or release_id is None:
        return []
    return list(
        await session.execute(
            select(
                FileReference.owner_node_id,
                FileReference.metadata_json["language"].astext.label("language"),
                FileReference.metadata_json["rover"].astext.label("rover"),
                FileReference.metadata_json["asset_version"].astext.label("asset_version"),
                FileReference.metadata_json["event_reference"].astext.label("event_reference"),
                FileReference.source_name,
                FileLocation.object_key,
            )
            .join(FileLocation, FileLocation.file_id == FileReference.file_id)
            .where(
                FileReference.owner_node_id.in_(owner_ids),
                FileReference.release_id == release_id,
                FileReference.reference_type == "story_sequence_audio",
                FileLocation.backend == "s3",
                FileLocation.bucket == bucket,
                FileLocation.available.is_(True),
                FileLocation.is_primary.is_(True),
            )
            .order_by(FileReference.id.desc())
        )
    )
