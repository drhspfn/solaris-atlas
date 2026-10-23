"""Game identities survive extraction workspace cleanup; blob presence never is assumed."""

import asyncio

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference


def target_identity(target: dict, reference_type: str | None = None) -> tuple:
    return (
        target.get("owner"), target["release_id"], target["path"], target.get("language"),
        reference_type or target.get("reference_type") or (
            "entity_image" if target["kind"] == "image" else "character_voice"
        ),
    )


def matches_source(metadata: dict, source: dict, target: dict) -> bool:
    provenance = metadata.get("game_source") or {}
    expected = {key: value for key, value in source.items()
                if key != "voice_plan_id" or target.get("kind") == "voice"}
    return (
        provenance.get("schema_version") == 1
        and provenance.get("package_path") == target["path"].split(".", 1)[0]
        and bool(provenance.get("source_files"))
        and all(provenance.get(key) == value for key, value in expected.items())
    )


async def missing_media_targets(session, targets: list[dict], *, source: dict, storage,
                                reference_type: str | None = None) -> list[dict]:
    """One database query, one HEAD per unique candidate blob, bounded concurrency.

    Missing/legacy provenance, other versions, and stale locations remain retryable.
    Storage errors propagate: a failed HEAD is not evidence of a missing object.
    Event/release workers can supply their reference type and the same source identity.
    """
    if not targets:
        return []
    rows = await session.execute(
        select(FileReference, FileObject, FileLocation)
        .join(FileObject, FileReference.file_id == FileObject.id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            FileReference.release_id.in_({t["release_id"] for t in targets}),
            FileReference.source_path.in_({t["path"] for t in targets}),
            FileLocation.backend == storage.backend,
            FileLocation.bucket == storage.bucket,
            FileLocation.available.is_(True),
        )
    )
    expected = {target_identity(t, reference_type): t for t in targets}
    candidates = []
    for reference, file, location in rows:
        identity = (reference.owner_node_id, reference.release_id, reference.source_path,
                    reference.source_name, reference.reference_type)
        target = expected.get(identity)
        if target is not None and matches_source(reference.metadata_json or {}, source, target):
            candidates.append((identity, file.size_bytes, location.object_key))
    semaphore = asyncio.Semaphore(16)

    async def stat(key):
        async with semaphore:
            return key, await storage.stat(key)

    objects = dict(await asyncio.gather(*(stat(key) for key in {c[2] for c in candidates})))
    present = {identity for identity, size, key in candidates
               if size is not None and objects[key] is not None and objects[key].size_bytes == size}
    return [t for t in targets if target_identity(t, reference_type) not in present]
