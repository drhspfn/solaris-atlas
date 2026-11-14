"""Recompile sequence metadata from verified retained JSON, without game export."""

import hashlib
import json
import zipfile
from pathlib import PurePosixPath

from sqlalchemy import select
from wuwa_story.db.models.storage import FileLocation, FileReference
from wuwa_story.ingestion.media_provenance import composition_retryable, matches_source
from wuwa_story.ingestion.spine_presentation import presentation_hash

from wuwa_story_worker.sequence_controls import sequence_control_score

# Compressed size of a retained bundle download. Real bundles are 80-200 MiB; decoded JSON deflates ~44:1.
MAX_BUNDLE_BYTES = 2 * 1024**3


def recompile_bundle(bundle, output, target):
    """Preserve extracted movies and incomplete-control evidence, not guessed media."""
    from wuwa_story_worker import bundle_io
    with bundle_io.spill_directory(output.parent) as spill:
        return _recompile_bundle(bundle, output, target, spill)


def _recompile_bundle(bundle, output, target, spill):
    from wuwa_story_worker import bundle_io
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        entries = bundle_io.load_entries(archive, manifest['files'], spill)
    presentation = json.loads(entries['presentation.json'])
    if (presentation.get('sha256') != target['presentation_sha256']
            or presentation_hash({k: v for k, v in presentation.items() if k != 'sha256'}) != target['presentation_sha256']):
        raise ValueError(
            'Retained sequence presentation differs from current target')
    unpacked = output.parent / 'retained-source'
    assets, originals = {}, []
    for name, data in entries.items():
        if name.startswith(('source/', 'media/')):
            path = bundle_io.materialize(data, unpacked / name)
            originals.append(path)
        if name.startswith('decoded/') and name.endswith('.json') and 'Client/Content/' in name:
            package = '/Game/' + \
                name.split('Client/Content/', 1)[1].removesuffix('.json')
            decoded = unpacked / 'decoded' / \
                hashlib.sha256(package.encode()).hexdigest()
            decoded.mkdir(parents=True, exist_ok=True)
            bundle_io.materialize(data, decoded / 'package.json')
            assets[package] = {'decoded': decoded}
    if not assets:
        raise ValueError(
            'Retained bundle has no source-keyed decoded sequence JSON')
    controls = json.loads(entries['controls.json'])
    score = sequence_control_score(assets, presentation)
    for unresolved in controls.get('unresolved', []):
        if unresolved not in score['unresolved']:
            score['unresolved'].append(unresolved)
    control_sources = dict(manifest.get('control_sources') or {})
    missing = list(control_sources.get('missing', []))
    for unresolved in score['unresolved']:
        diagnostic = {'status': 'unresolved_control', **unresolved}
        if diagnostic not in missing:
            missing.append(diagnostic)
    for event in score['events']:
        diagnostics = [d for d in event.get('diagnostics', []) if d.get(
            'code') != 'unbound_event_function']
        if diagnostics:
            diagnostic = {'status': 'unsupported_control',
                          'source': event['source'], 'diagnostics': diagnostics}
            if diagnostic not in missing:
                missing.append(diagnostic)
    manifest['control_sources'] = {**control_sources, 'missing': missing}
    previous = {section['source']: section for section in controls.get('media', {}).get('sections', [])
                if section.get('kind') == 'movie_fragment'}
    for section in score['media']['sections']:
        old = previous.get(section['source'])
        if section['kind'] != 'movie_fragment' or not old:
            continue
        if any(section.get(key) != old.get(key) for key in ('reference', 'scene_source', 'clock', 'range', 'parameters')):
            raise ValueError(
                'Retained movie section no longer matches its authored timing')
        if old.get('file') and old['file'] not in entries:
            raise ValueError(
                'Retained movie section has no verified archive entry')
        section.update({key: old[key] for key in (
            'file', 'archive_path', 'status') if key in old})
    if set(previous) - {section['source'] for section in score['media']['sections'] if section['kind'] == 'movie_fragment'}:
        raise ValueError('Metadata rebuild lost an existing movie section')
    entries['controls.json'] = json.dumps(
        score, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    manifest['sequence_metadata_revision'] = target['sequence_metadata_revision']
    manifest['files']['controls.json'] = {'size_bytes': len(entries['controls.json']),
                                          'sha256': hashlib.sha256(entries['controls.json']).hexdigest()}
    entries['manifest.json'] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.partial')
    bundle_io.write_archive(temporary, entries)
    temporary.replace(output)
    if target.get('sequence_metadata_revision', 0) >= 5:
        from wuwa_story_worker.sequence_composition import annotate_bundle
        annotate_bundle(output)
    return output, sorted(originals)


async def retained_bundle(session, storage, root, target, source):
    """An exact-source prior bundle is input to the normal publisher, never reused as new proof."""
    if not target.get('sequence_metadata_revision'):
        return None
    rows = await session.execute(select(FileReference.metadata_json, FileLocation.object_key).join(
        FileLocation, FileLocation.file_id == FileReference.file_id).where(
        FileReference.owner_node_id == target['owner'], FileReference.release_id == target['release_id'],
        FileReference.source_path == target['path'], FileReference.reference_type == target['reference_type'],
        FileReference.source_name == target.get(
            'source_name', target['language']),
        FileLocation.backend == storage.backend, FileLocation.bucket == storage.bucket,
        FileLocation.available.is_(True), FileLocation.is_primary.is_(True),
    ).order_by(FileReference.id.desc()))
    for metadata, key in rows:
        proof = (metadata or {}).get('game_source') or {}
        # Revision 4 changes which reachable control packages are extracted.
        # Re-scoring an older ZIP cannot supply newly discovered source bytes.
        if target['sequence_metadata_revision'] >= 4 and (proof.get('sequence_metadata_revision') or 0) < 4:
            continue
        # This checks source identity only. The original manifest's incomplete
        # controls remain untouched and the normal publisher still reports partial.
        # Older JSON archives predate soundtrack decoding. Check their extraction
        # tools here; the new audio renderer records its own current tool hashes.

        def extraction_tools(values):
            return {key: value for key, value in values.items()
                    if key not in {'WUWA_VGMSTREAM_PATH', 'WUWA_WWISER_PATH'}}
        identity = {**metadata, 'game_source': {**proof, 'control_sources_complete': True,
                    'tools': extraction_tools(proof.get('tools', {}))}}
        extraction_source = {
            **source, 'tools': extraction_tools(source.get('tools', {}))}
        extraction_target = {key: value for key, value in target.items(
        ) if key != 'soundtrack_context_sha256'}
        extraction_target['sequence_metadata_revision'] = None
        if not matches_source(identity, extraction_source, extraction_target):
            continue
        context_changed = ('soundtrack_context_sha256' in target and
                           proof.get('soundtrack_context_sha256') != target['soundtrack_context_sha256'])
        if ((proof.get('sequence_metadata_revision') or 0) >= target['sequence_metadata_revision']
                and not context_changed and not composition_retryable(metadata or {})):
            return None
        path = root / 'sequence-metadata' / \
            str(target['owner']) / 'retained.zip'
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open('wb') as output:
            async for chunk in storage.get(key):
                count += len(chunk)
                if count > MAX_BUNDLE_BYTES:
                    raise ValueError(
                        'Retained sequence download exceeds metadata rebuild bound')
                output.write(chunk)
        if proof.get('control_sources_complete') is False:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            if not (manifest.get('control_sources') or {}).get('missing'):
                raise ValueError(
                    'Incomplete provenance has no retained missing-control evidence')
        return path
    return None
