"""Extract explicitly referenced sequence movie fragments, never a whole-scene video."""

import asyncio
import copy
import re
import zipfile
from pathlib import Path, PurePosixPath

from wuwa_story_worker.archive_index import (
    ensure_archive_index,
    indexed_package_paths,
    voice_filter_batches,
)
from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.cutscene_sources import prepare_base_movies


async def prepare_sequence_fragments(root, media, fmodel):
    # Keep the parser's evidence reusable; extraction adds availability, not timing.
    media = copy.deepcopy(media)
    media['complete_video'] = False
    sections = [section for section in media.get(
        'sections', []) if section.get('kind') == 'movie_fragment']
    if not sections:
        return {'media': media, 'files': {}, 'missing': []}
    references = {}
    for section in sections:
        reference = section.get('reference')
        # A decoded ObjectPath can include an object name or numeric export index.
        package = reference.split('.', 1)[0] if isinstance(
            reference, str) else ''
        if (isinstance(reference, str)
                and re.fullmatch(r'/Game/Aki/[a-zA-Z0-9_ /-]+(?:\.[a-zA-Z0-9_]+)?', reference)
                and not package.endswith('/')):
            references[reference] = package.removeprefix('/Game/')
    if not references:
        for section in sections:
            section.pop('file', None)
            section['archive_path'] = None
            section['status'] = 'unresolved_media_source'
        return {'media': media, 'files': {}, 'missing': [
            {'expected': section['source'], 'status': 'unresolved_media_source'} for section in sections]}

    index = await ensure_archive_index(root, fmodel, root)
    available = await asyncio.to_thread(indexed_package_paths, index, references.values())
    receipts, errors = [], {}

    async def export_wrappers(wrappers):
        # Include extensions: FModel's substring filter must not select aliases.
        paths = [str(PurePosixPath(wrapper).with_suffix(suffix))
                 for wrapper in wrappers for suffix in ('.uasset', '.uexp')]
        return await export_assets(root, fmodel, '|'.join(paths))

    for batch in voice_filter_batches(available.values()):
        try:
            receipts.append(await export_wrappers(batch))
        except RuntimeError:
            for wrapper in batch:
                try:
                    receipts.append(await export_wrappers([wrapper]))
                except RuntimeError as error:
                    errors[wrapper] = str(error)
    from wuwa_story_worker.release_media import merge_exports

    assets = await asyncio.to_thread(merge_exports, receipts, root / 'sequence-fragments' / 'files')
    wrappers = sorted(set(available.values()) - errors.keys())
    movies = await prepare_base_movies(root, assets, wrappers, fmodel, index=index) if wrappers else {
        'receipts': [], 'movies': {}, 'errors': {},
    }
    if movies['receipts']:
        await asyncio.to_thread(merge_exports, movies['receipts'], assets)
    errors.update(movies.get('errors', {}))
    files, missing = {}, []
    for section in sections:
        package = references.get(section.get('reference'))
        wrapper = available.get(package)
        movie = movies['movies'].get(wrapper)
        section.pop('file', None)
        section['archive_path'] = movie
        if movie and (assets / movie).is_file():
            section['status'] = 'extracted'
            section['file'] = 'media/' + movie
            files[movie] = assets / movie
        else:
            section['status'] = 'media_export_failed' if wrapper in errors or movie in errors else 'media_not_in_client'
            missing.append({'expected': movie or section.get('reference') or section['source'],
                            'source': section['source'], 'status': section['status']})
    return {'media': media, 'files': files, 'missing': missing}


async def publish_sequence_fragments(session, bundle, target, published, service, run_id, provenance):
    """Publish verified individual MP4s; retain authored section clocks and provenance."""
    import hashlib
    import json

    from sqlalchemy import select
    from wuwa_story.db.models.storage import FileObject, FileReference, FileType
    from wuwa_story.storage.hashing import content_object_key

    def verified(archive, manifest, entry):
        expected = manifest['files'][entry]
        if (expected['size_bytes'] > 256 * 1024 * 1024
                or archive.getinfo(entry).file_size != expected['size_bytes']):
            raise ValueError(
                'Sequence fragment exceeds its verified archive size')
        data = archive.read(entry)
        if hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError(
                'Sequence fragment does not match its archive manifest')
        return data, expected

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        if 'controls.json' not in manifest.get('files', {}):
            return None
        controls, _ = verified(archive, manifest, 'controls.json')
        media = json.loads(controls).get('media')
        if not isinstance(media, dict):
            return None
        if any(manifest.get(key) != provenance[key] for key in ('asset_version', 'download_id')):
            raise ValueError(
                'Sequence fragments do not match the pinned client provenance')
        media = {**copy.deepcopy(media), 'schema_version': 1, 'complete_video': False,
                 'game_source': copy.deepcopy(provenance), 'components': []}
        files = {}
        for section in media.get('sections', []):
            entry = section.get('file')
            if section.get('kind') != 'movie_fragment' or not entry:
                continue
            path = section.get('archive_path')
            if (not isinstance(path, str) or not path.startswith('Client/Content/Aki/Movies/')
                    or not path.endswith('.mp4') or '..' in PurePosixPath(path).parts
                    or '\\' in path or entry != 'media/' + path):
                raise ValueError(
                    'Expected an exact authored movie fragment archive entry')
            if entry not in files:
                data, expected = verified(archive, manifest, entry)
                digest = bytes.fromhex(expected['sha256'])
                output = Path(bundle).parent / \
                    'sequence-playback' / expected['sha256']
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                # Raw movies may already have been registered by the CG pipeline.
                # Preserve their content identity and existing storage file type.
                kind = await session.scalar(select(FileType.key).join(
                    FileObject, FileObject.file_type_id == FileType.id).where(FileObject.sha256 == digest))
                file = await service.register_file(session, output, kind or 'video_game', mime_type='video/mp4')
                await service.register_variant(session, published.id, file.id, 'story_sequence_fragment', run_id)
                reference = await session.scalar(select(FileReference).where(
                    FileReference.owner_node_id == target['owner'],
                    FileReference.release_id == target['release_id'],
                    FileReference.reference_type == 'story_sequence_fragment',
                    FileReference.source_path == target['path'], FileReference.source_name == entry,
                    FileReference.metadata_json['asset_version'].astext == provenance['asset_version'],
                    FileReference.metadata_json['download_id'].astext == provenance['download_id']))
                if reference is None:
                    reference = await service.register_reference(session, file_id=file.id,
                                                                 reference_type='story_sequence_fragment', owner_node_id=target['owner'],
                                                                 release_id=target['release_id'], source_path=target['path'], source_name=entry)
                reference.file_id = file.id
                reference.metadata_json = {**(reference.metadata_json or {}),
                                           'asset_version': provenance['asset_version'], 'download_id': provenance['download_id'],
                                           'bundle_file_id': published.id, 'archive_entry': entry, 'archive_path': path,
                                           'game_source': copy.deepcopy(provenance),
                                           'sections': [part['source'] for part in media['sections'] if part.get('file') == entry]}
                files[entry] = {'file_id': file.id, 'object_key': content_object_key(file.sha256),
                                'size_bytes': len(data), 'mime_type': 'video/mp4', 'sha256': file.sha256.hex()}
                media['components'].append(files[entry])
            section.update(files[entry])
            section['status'] = 'published'
        return media


async def invalidate_sequence_quests(session, target):
    """Flow owners have no read model; dirty their exact release-evidenced quests."""
    from sqlalchemy import select
    from wuwa_story.db.repositories.quest_scope import quest_state_links
    from wuwa_story.read_models.refresh import mark_dirty

    links = quest_state_links(target['release_id'], state_ids=[target['owner']])
    quests = sorted(set(await session.scalars(select(links.c.quest_id).distinct())))
    if quests:
        await mark_dirty(session, 'quest', quests)
        await mark_dirty(session, 'quest', quests, f"release:{target['release_id']}")
    return quests
