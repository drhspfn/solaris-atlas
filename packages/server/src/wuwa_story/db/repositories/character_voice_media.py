"""Named authored alternatives; no implicit choice of Rover state/random child."""

from types import SimpleNamespace

from sqlalchemy import select

from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.ingestion.media_provenance import voice_variant_group
from wuwa_story.storage.s3 import S3Storage


async def character_voice_alternatives(session, owner_id, release_id, paths, settings):
    paths = sorted({path for path in paths if isinstance(path, str) and path})
    if not paths:
        return {}
    # Hydrate only the small selector fragment, not EventCookedData/source metadata.
    rows = await session.execute(
        select(FileReference.id, FileReference.source_path, FileReference.source_name,
               FileReference.metadata_json['game_source']['voice_variant'].label('proof'),
               FileObject.mime_type, FileLocation.object_key)
        .join(FileObject, FileObject.id == FileReference.file_id)
        .outerjoin(FileLocation, (FileLocation.file_id == FileObject.id)
                   & FileLocation.available.is_(True) & FileLocation.is_primary.is_(True)
                   & (FileLocation.backend == 's3') & (FileLocation.bucket == settings.s3_bucket))
        .where(FileReference.owner_node_id == owner_id, FileReference.release_id == release_id,
               FileReference.reference_type == 'character_voice', FileReference.source_path.in_(paths),
               FileReference.source_name.like('%:hirc1:%'))
        .order_by(FileReference.id.desc())
    )
    groups, candidates = {}, {}
    for _identity, path, name, proof, mime, key in rows:
        reference = SimpleNamespace(source_name=name, metadata_json={'game_source': {'voice_variant': proof}})
        group = voice_variant_group(reference)
        if group is None:
            continue
        language, names, fingerprints = group
        group_key = (path, language)
        groups.setdefault(group_key, (names, fingerprints))
        if groups[group_key] != (names, fingerprints):
            continue
        candidates.setdefault((path, name), (proof['variant'], key if mime in
                              {'audio/wav', 'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/webm', 'audio/aac'} else None))
    if not groups:
        return {}
    storage = S3Storage(settings)
    result = {}
    for (path, language), (names, _) in sorted(groups.items()):
        for name in names:
            variant, key = candidates.get((path, name), ({'id': name.rsplit(':', 1)[-1]}, None))
            result.setdefault(path, []).append({**variant, 'source_name': name,
                'language': 'zh-Hans' if language == 'zh' else language,
                'url': storage.public_url(key) if key else None,
                'status': 'available' if key else 'missing'})
    return result
