"""Game identities survive extraction workspace cleanup; blob presence never is assumed."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference


def authored_package_path(target: dict) -> str:
    path = target.get("package_path") or target["path"]
    return path.split(".", 1)[0] if path.startswith("/Game/") else path


def target_identity(target: dict, reference_type: str | None = None) -> tuple:
    return (
        target.get("owner"), target["release_id"], target["path"],
        target.get("source_name", target.get("language")),
        reference_type or target.get("reference_type") or (
            "entity_image" if target["kind"] == "image" else "character_voice"
        ),
    )


def matches_source(metadata: dict, source: dict, target: dict) -> bool:
    provenance = metadata.get("game_source") or {}
    files = provenance.get("source_files") or []
    expected = {key: value for key, value in source.items()
                if key != "tools" and (key != "voice_plan_id" or target.get("kind") != "image")}
    tools = {key: value for key, value in source.get("tools", {}).items()
             if key != "WUWA_VGMSTREAM_PATH" or target.get("kind") != "image"}
    return (
        (target.get('kind') != 'spine' or provenance.get(
            'spine_recipe') == target.get('spine_recipe'))
        and provenance.get("schema_version") == 1
        and provenance.get("package_path") == authored_package_path(target)
        and bool(files)
        and all(isinstance(file, dict) and file.get("package_path")
                and isinstance(file.get("sha256"), str) and len(file["sha256"]) == 64
                for file in files)
        and all(provenance.get(key) == value for key, value in expected.items())
        and all((provenance.get("tools") or {}).get(key) == value for key, value in tools.items())
    )


async def missing_media_targets(session, targets: list[dict], *, source: dict, storage,
                                reference_type: str | None = None) -> list[dict]:
    """One database query, one HEAD per unique candidate blob, bounded concurrency.

    Missing/legacy provenance, other versions, and stale locations remain retryable.
    Storage errors propagate: a failed HEAD is not evidence of a missing object.
    Targets carry path, release_id (possibly None), optional owner, language and
    source_name (for localized filenames/derived roles), kind, and reference_type.
    package_path can override an upstream alias with the exact game package path.
    source contains pinned client/package IDs, versions, tools and, for derived
    media, a recipe hash. Matching blobs can create a new release reference;
    the caller commits these references with its job result.
    """
    if not targets:
        return []
    rows = await session.execute(
        select(FileReference, FileObject, FileLocation)
        .join(FileObject, FileReference.file_id == FileObject.id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            FileReference.source_path.in_({t["path"] for t in targets}),
            FileLocation.backend == storage.backend,
            FileLocation.bucket == storage.bucket,
        )
    )
    expected = {target_identity(t, reference_type): t for t in targets}
    by_source = {(key[0], *key[2:]): target for key,
                 target in expected.items()}
    candidates = []
    references = {}
    for reference, file, location in rows:
        identity = (reference.owner_node_id, reference.release_id, reference.source_path,
                    reference.source_name, reference.reference_type)
        references.setdefault(identity, []).append(reference)
        target = by_source.get((identity[0], *identity[2:]))
        metadata = reference.metadata_json or {}
        playable = target is None or target.get('kind') != 'spine' or ((metadata.get('playback') or {}).get('variants') and (metadata.get('playback') or {}).get('components') and not (metadata.get('playback') or {}).get('unsupported'))
        if target is not None and playable and matches_source(metadata, source, target):
            candidates.append((target_identity(target, reference_type), file.size_bytes,
                               location.object_key, reference, location))
    semaphore = asyncio.Semaphore(16)

    async def stat(key):
        async with semaphore:
            return key, await storage.stat(key)

    components = {key: (reference.metadata_json or {}).get('playback', {}).get('components', [])
                  for _identity, _size, key, reference, _location in candidates
                  if expected[_identity].get('kind') == 'spine'}
    keys = {c[2] for c in candidates} | {part['object_key'] for parts in components.values() for part in parts}
    objects = dict(await asyncio.gather(*(stat(key) for key in keys)))
    present = set()
    for identity, size, key, reference, location in candidates:
        location.verified_at = datetime.now(UTC)
        if size is None or objects[key] is None or objects[key].size_bytes != size:
            location.available = False
            continue
        location.available = True
        if any(objects[part['object_key']] is None or objects[part['object_key']].size_bytes != part['size_bytes']
               for part in components.get(key, [])):
            continue
        present.add(identity)
        current = next((ref for ref in references.get(identity, [])
                        if matches_source(ref.metadata_json or {}, source, expected[identity])), None)
        if current is not None:
            current.file_id = reference.file_id
            current.metadata_json = dict(reference.metadata_json)
        else:
            current = FileReference(
                owner_node_id=identity[0], release_id=identity[1], source_path=identity[2],
                source_name=identity[3], reference_type=identity[4], file_id=reference.file_id,
                metadata_json=dict(reference.metadata_json),
            )
            session.add(current)
            references.setdefault(identity, []).append(current)
    return [t for t in targets if target_identity(t, reference_type) not in present]
