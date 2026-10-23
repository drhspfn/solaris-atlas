"""Worker publication evidence and verification of every required durable blob."""

import asyncio
import hashlib
import json

from sqlalchemy import select
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.ingestion.media_provenance import matches_source

from wuwa_story_worker.media_provenance import source_provenance


def pinned_source(context: dict, asset_version: str | None = None) -> dict:
    source = {key: context[key] for key in (
        "asset_version", "download_id", "tier", "voice_plan_id", "game_version", "tools",
        "video_plan_id",
    ) if key in context}
    if any(not source.get(key) for key in ("asset_version", "download_id", "tier", "tools")):
        raise ValueError("Source context requires pinned asset_version, download_id, tier and tools")
    if asset_version is not None and source["asset_version"] != asset_version:
        raise ValueError("Source context asset version does not match publication")
    return source


def recipe_identity(spec, analyze_variants: bool = False) -> str:
    body = {"recipe": spec.model_dump(mode="json"), "analyze_variants": analyze_variants}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def publication_source(context, target, originals, **details):
    source = pinned_source(context)
    result = source_provenance(source, target, originals, source["tools"], details)
    result.update(source)
    # Archive keys are game manifest paths/IDs, never workspace filenames or encryption keys.
    for key in ("archive_keys", "package_paths", "path_map"):
        if key in context:
            result[key] = context[key]
    return result


async def verified_references(session, storage, kind, source, paths=None, owner_ids=None):
    """Return compatible references only when their complete file closure still exists.

    Errors from S3 propagate; unavailable objects and size mismatches are retryable.
    Legacy references without a closure are deliberately never considered complete.
    """
    query = select(FileReference).where(FileReference.reference_type == kind)
    if paths is not None:
        query = query.where(FileReference.source_path.in_(paths))
    if owner_ids is not None:
        query = query.where(FileReference.owner_node_id.in_(owner_ids))
    candidates = []
    for ref in await session.scalars(query):
        metadata = ref.metadata_json or {}
        provenance = metadata.get("game_source") or {}
        target = {"path": ref.source_path, "kind": "voice",
                  "package_path": provenance.get("package_path")}
        ids = metadata.get("publication_file_ids")
        if (ref.source_path and ids and ref.file_id in ids
                and matches_source(metadata, source, target)):
            candidates.append(ref)
    ids = {identity for ref in candidates for identity in ref.metadata_json["publication_file_ids"]}
    if not ids:
        return []
    required_refs = {item["id"] for ref in candidates
                     for item in ref.metadata_json.get("publication_references", [])}
    current_refs = dict(list(await session.execute(select(FileReference.id, FileReference.file_id)
                                              .where(FileReference.id.in_(required_refs))))) if required_refs else {}
    candidates = [ref for ref in candidates if all(
        current_refs.get(item["id"]) == item["file_id"]
        for item in ref.metadata_json.get("publication_references", []))]
    rows = list(await session.execute(
        select(FileObject.id, FileObject.size_bytes, FileLocation)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(FileObject.id.in_(ids), FileLocation.backend == storage.backend,
               FileLocation.bucket == storage.bucket)
    ))
    semaphore = asyncio.Semaphore(16)

    async def stat(key):
        async with semaphore:
            return key, await storage.stat(key)

    objects = dict(await asyncio.gather(*(stat(key) for key in {row[2].object_key for row in rows})))
    present = set()
    for identity, size, location in rows:
        stored = objects[location.object_key]
        location.available = size is not None and stored is not None and stored.size_bytes == size
        if location.available:
            present.add(identity)
    return [ref for ref in candidates
            if set(ref.metadata_json["publication_file_ids"]).issubset(present)]
