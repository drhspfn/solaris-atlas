"""Game identities survive extraction workspace cleanup; blob presence never is assumed."""

import asyncio
import re
from datetime import UTC, datetime

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference


def composition_retryable(metadata: dict) -> bool:
    composition = (metadata.get('sequence_media') or {}).get('composition') or {}
    return (composition.get('state') == 'failed'
            or any(reason.get('code') == 'renderer_disabled'
                   for reason in composition.get('reasons', []) if isinstance(reason, dict)))


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
    revision = target.get('sequence_metadata_revision')
    sequence_target = target.get('path', '').startswith(
        'story_sequence:') or (type(revision) is int and revision >= 5)
    sequence_audio = (sequence_target and type(revision) is int and revision >= 3
                      or bool(target.get('soundtrack_context')))
    tools = {key: value for key, value in source.get("tools", {}).items()
             if (key != "WUWA_VGMSTREAM_PATH" or target.get("kind") != "image" or sequence_audio)
             and (key != 'WUWA_WWISER_PATH' or provenance.get('voice_variant') or sequence_audio)}
    return (
        ('soundtrack_context_sha256' not in target
         or provenance.get('soundtrack_context_sha256') == target['soundtrack_context_sha256'])
        and
        (not sequence_target or target.get('sequence_metadata_revision') is None
         or provenance.get('sequence_metadata_revision') == target['sequence_metadata_revision'])
        and
        (target.get('kind') != 'spine' or provenance.get(
            'spine_recipe') == target.get('spine_recipe') and provenance.get(
            'presentation_sha256') == target.get('presentation_sha256') and
         (not target.get('sequence_media_schema') or provenance.get('sequence_media_schema') == target['sequence_media_schema']) and
         provenance.get('control_sources_complete', True))
        and provenance.get("schema_version") == 1
        and provenance.get("package_path") == authored_package_path(target)
        and bool(files)
        and all(isinstance(file, dict) and file.get("package_path")
                and isinstance(file.get("sha256"), str) and len(file["sha256"]) == 64
                for file in files)
        and all(provenance.get(key) == value for key, value in expected.items())
        and all((provenance.get("tools") or {}).get(key) == value for key, value in tools.items())
    )


def voice_variant_group(reference):
    """A complete set is evidence, never a language fallback to one variant."""
    proof = ((reference.metadata_json or {}).get('game_source') or {}).get('voice_variant') or {}
    if not isinstance(proof, dict):
        return None
    variant = proof.get('variant') or {}
    if not isinstance(variant, dict):
        return None
    language = variant.get('language')
    names = proof.get('expected_source_names')
    if (proof.get('schema') != 1 or not isinstance(language, str) or language not in {'en', 'ja', 'ko', 'zh'}
            or not isinstance(names, list) or not 1 < len(names) <= 64
            or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names) or reference.source_name != variant.get('source_name')
            or reference.source_name not in names
            or any(not isinstance(name, str) or not re.fullmatch(language + r':hirc1:[0-9a-f]{24}', name)
                   for name in names)
            or any(not isinstance(proof.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', proof[key])
                   for key in ('bank_sha256', 'init_sha256', 'wwiser_sha256'))):
        return None
    return language, tuple(sorted(names)), tuple(proof[key] for key in
                                               ('bank_sha256', 'init_sha256', 'wwiser_sha256'))


async def missing_media_targets(session, targets: list[dict], *, source: dict, storage,
                                reference_type: str | None = None, require_spine_playback: bool = False) -> list[dict]:
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
    rows = list(rows)
    expected = {target_identity(t, reference_type): t for t in targets}
    by_source = {(key[0], *key[2:]): target for key,
                 target in expected.items()}
    candidates = []
    references = {}
    variant_groups = {}
    # The newest valid proof defines one coherent set. Older banks/recipes must
    # not fill holes in that set, even when the numeric media IDs were reused.
    for reference, _file, _location in rows:
        group = voice_variant_group(reference) if reference.reference_type == 'character_voice' else None
        if group is None:
            continue
        language, names, fingerprints = group
        base = by_source.get((reference.owner_node_id, reference.source_path, language, reference.reference_type))
        if base is None or base.get('kind') != 'voice' or not matches_source(reference.metadata_json or {}, source, base):
            continue
        identity = target_identity(base, reference_type)
        current = variant_groups.get(identity)
        if current is None or reference.id > current[0]:
            variant_groups[identity] = (reference.id, group)
    for reference, file, location in rows:
        identity = (reference.owner_node_id, reference.release_id, reference.source_path,
                    reference.source_name, reference.reference_type)
        references.setdefault(identity, []).append(reference)
        target = by_source.get((identity[0], *identity[2:]))
        group = voice_variant_group(reference) if reference.reference_type == 'character_voice' else None
        if target is None and group is not None:
            language, names, _fingerprints = group
            base = by_source.get((identity[0], identity[2], language, identity[4]))
            base_identity = target_identity(base, reference_type) if base else None
            selected = variant_groups.get(base_identity)
            if selected is not None and selected[1] == group:
                target = {**base, 'source_name': reference.source_name}
                expected[target_identity(target, reference_type)] = target
        metadata = reference.metadata_json or {}
        if target is not None:
            scoped_audio = [section for section in (metadata.get('sequence_media') or {}).get('sections', [])
                            if isinstance(section, dict) and section.get('kind') == 'audio_event'
                            and section.get('scope') == 'flow']
            if scoped_audio and (reference.release_id != target.get('release_id') or any(
                   not isinstance(section.get('source_proof'), dict)
                   or section['source_proof'].get('release_id') != target.get('release_id')
                   or section['source_proof'].get('flow_owner') != target.get('owner')
                   for section in scoped_audio)):
                continue
        composition = (metadata.get('sequence_media') or {}).get('composition') or {}
        sequence = target is not None and (target.get('path', '').startswith('story_sequence:')
                                           or target.get('sequence_metadata_revision') is not None)
        if sequence and composition_retryable(metadata):
            continue
        illustrated = sequence and composition.get('state') in {'ready', 'unsupported'}
        preview = metadata.get('animation_preview') or {}
        native_ready = preview.get('recipe') in {'riglyph-native-v1', 'riglyph-native-v2'} and preview.get('status') == 'ready' and preview.get('variants')
        playable = not require_spine_playback or target is None or target.get('kind') != 'spine' or native_ready or illustrated or ((metadata.get('playback') or {}).get('variants') and (metadata.get('playback') or {}).get('components') and not (metadata.get('playback') or {}).get('unsupported'))
        if target is not None and playable and matches_source(metadata, source, target):
            candidates.append((target_identity(target, reference_type), file.size_bytes,
                               location.object_key, reference, location))
    semaphore = asyncio.Semaphore(16)

    async def stat(key):
        async with semaphore:
            return key, await storage.stat(key)

    components = {}
    for identity, _size, key, reference, _location in candidates:
        metadata = reference.metadata_json or {}
        parts = list((metadata.get('sequence_media') or {}).get('components', []))
        if require_spine_playback and expected[identity].get('kind') == 'spine':
            parts.extend((metadata.get('animation_preview') or {}).get('variants', [])
                         or (metadata.get('playback') or {}).get('components', []))
        components.setdefault(key, []).extend(parts)
    keys = {c[2] for c in candidates} | {part['object_key'] for parts in components.values() for part in parts}
    objects = dict(await asyncio.gather(*(stat(key) for key in keys)))
    present = set()
    linked_characters = {}
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
            if identity[4] == 'character_voice' and voice_variant_group(reference) is not None:
                linked_characters.setdefault(identity[1], set()).add(identity[0])
    for identity, (_id, (_language, names, _fingerprints)) in variant_groups.items():
        if all((identity[0], identity[1], identity[2], name, identity[4]) in present for name in names):
            present.add(identity)
        else:
            # A legacy single-language clip cannot satisfy a proven multi-variant set.
            present.discard(identity)
    if linked_characters:
        from wuwa_story.db.models.ops import GameRelease
        from wuwa_story.read_models.refresh import mark_dirty

        releases = dict((await session.execute(select(GameRelease.id, GameRelease.game_version)
                        .where(GameRelease.id.in_(linked_characters)))).all())
        for release_id, owners in sorted(linked_characters.items()):
            if release_id not in releases:
                raise ValueError('Unknown character voice publication release')
            await mark_dirty(session, 'character', sorted(owners))
            await mark_dirty(session, 'character', sorted(owners), releases[release_id])
    return [t for t in targets if target_identity(t, reference_type) not in present]
