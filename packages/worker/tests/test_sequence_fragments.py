import copy
import hashlib
import json
import sqlite3
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import cutscene_sources, sequence_fragments, story_spine


@pytest.fixture
def fragment_client(tmp_path, monkeypatch):
    sources = {}
    for name in ('M0334_003', 'M0334_003d'):
        wrapper = f'Client/Content/Aki/Sequence/M0334/{name}'
        movie = f'Client/Content/Aki/Movies/Actual/{name}.mp4'
        sources[wrapper + '.uasset'] = b'wrapper header'
        sources[wrapper + '.uexp'] = b'filepath://./' + movie.removeprefix('Client/Content/').encode() + b'\0'
        sources[movie] = b'exact movie bytes: ' + name.encode()
    index = tmp_path / 'archive-index.sqlite'
    with sqlite3.connect(index) as db:
        db.execute('CREATE TABLE files (name TEXT, path TEXT)')
        db.executemany('INSERT INTO files VALUES (?,?)', [(path.rsplit('/', 1)[-1].lower(), path) for path in sources])
    monkeypatch.setattr(sequence_fragments, 'ensure_archive_index', AsyncMock(return_value=index))
    requested = []

    async def export(root, tool, filters):
        requested.append(filters)
        out = root / 'exports' / hashlib.sha256(filters.encode()).hexdigest()
        receipt = out / 'manifest.json'
        if not receipt.exists():
            for name in filters.split('|'):
                path = out / 'files' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(sources[name])
            receipt.write_text('{}')
        return receipt

    monkeypatch.setattr(sequence_fragments, 'export_assets', export)
    monkeypatch.setattr(cutscene_sources, 'export_assets', export)
    return sources, requested


def score():
    return {'complete_video': False, 'runtime_traversal': False, 'sections': [
        {'source': f'/Game/Aki/Sequence/M0334/M0334_All.{410 + index}',
         'kind': 'movie_fragment', 'reference': f'/Game/Aki/Sequence/M0334/{name}',
         'clock': {'numerator': 30, 'denominator': 1}, 'range': {'authored': [152, 374]},
         'parameters': {'StartFrameOffset': {'Value': 102}},
         'archive_path': None, 'status': 'unresolved_media_source'}
        for index, name in enumerate(('M0334_003', 'M0334_003d', 'M0334_003'))
    ]}


@pytest.mark.asyncio
async def test_exact_wrappers_extract_all_fragments_and_replay_with_section_provenance(tmp_path, fragment_client):
    sources, requested = fragment_client
    media = score()
    untouched = copy.deepcopy(media)
    first = await sequence_fragments.prepare_sequence_fragments(tmp_path, media, tmp_path / 'tool')
    second = await sequence_fragments.prepare_sequence_fragments(tmp_path, media, tmp_path / 'tool')
    assert first == second and media == untouched
    assert first['missing'] == [] and len(first['files']) == 2
    assert first['media']['complete_video'] is False
    for original, section in zip(media['sections'], first['media']['sections'], strict=True):
        assert all(section[key] == original[key] for key in ('source', 'reference', 'clock', 'range', 'parameters'))
        assert section['status'] == 'extracted'
        assert section['file'] == 'media/' + section['archive_path']
        assert first['files'][section['archive_path']].read_bytes() == sources[section['archive_path']]
    # Only explicitly indexed wrappers and the two URLs they contain; no prefix/full-game filters.
    assert {path for filters in requested for path in filters.split('|')} == set(sources)


@pytest.mark.asyncio
async def test_invalid_wrapper_does_not_discard_valid_sibling(tmp_path, fragment_client):
    sources, _ = fragment_client
    sources['Client/Content/Aki/Sequence/M0334/M0334_003d.uexp'] = b'no authoritative movie URL'
    result = await sequence_fragments.prepare_sequence_fragments(tmp_path, score(), tmp_path / 'tool')
    assert len(result['files']) == 1
    assert result['media']['sections'][1]['status'] == 'media_export_failed'
    assert result['missing'][0]['source'].endswith('.411')
    assert all(section['status'] == 'extracted' for section in result['media']['sections'][::2])


@pytest.mark.asyncio
async def test_failed_wrapper_batch_retries_individual_packages(tmp_path, fragment_client, monkeypatch):
    original_export = sequence_fragments.export_assets

    async def export(root, tool, filters):
        if filters.count('.uasset') > 1 or 'M0334_003d.uasset' in filters:
            raise RuntimeError('wrapper extraction failed')
        return await original_export(root, tool, filters)

    monkeypatch.setattr(sequence_fragments, 'export_assets', export)
    result = await sequence_fragments.prepare_sequence_fragments(tmp_path, score(), tmp_path / 'tool')
    assert len(result['files']) == 1
    assert result['media']['sections'][1]['status'] == 'media_export_failed'
    assert result['media']['sections'][0]['status'] == 'extracted'


@pytest.mark.asyncio
async def test_missing_or_unsafe_sources_do_not_invent_movies(tmp_path, fragment_client, monkeypatch):
    media = score()
    for section in media['sections']:
        section['reference'] = '/Game/Aki/../escape'
    result = await sequence_fragments.prepare_sequence_fragments(tmp_path, media, tmp_path / 'tool')
    assert result['files'] == {} and len(result['missing']) == 3
    sequence_fragments.ensure_archive_index.assert_not_awaited()
    for section in media['sections']:
        section['reference'] = '/Game/Aki/Sequence/WrongFolder/M0334_003'
    result = await sequence_fragments.prepare_sequence_fragments(tmp_path, media, tmp_path / 'tool')
    assert result['files'] == {} and all(s['status'] == 'media_not_in_client' for s in result['media']['sections'])


@pytest.mark.asyncio
async def test_actual_fragments_are_deduplicated_in_portable_bundle(tmp_path, fragment_client):
    result = await sequence_fragments.prepare_sequence_fragments(tmp_path, score(), tmp_path / 'tool')
    resource = {'resource_id': None, 'controller_archive': True, 'variants': [], 'control_score': {'media': result['media']}}
    output, originals = story_spine.portable_bundle(tmp_path / 'scene.zip', resource, {}, {},
        {'asset_version': '3.7.1', 'download_id': 'pinned-client'}, movie_fragments=result['files'])
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as archive:
        controls = json.loads(archive.read('controls.json'))
        manifest = json.loads(archive.read('manifest.json'))
        assert controls['media']['complete_video'] is False
        assert len([path for path in archive.namelist() if path.endswith('.mp4')]) == 2
        for section in controls['media']['sections']:
            data = archive.read(section['file'])
            assert data == result['files'][section['archive_path']].read_bytes()
            assert manifest['files'][section['file']]['sha256'] == hashlib.sha256(data).hexdigest()
    assert set(originals) == set(result['files'].values())
    # Compare identical inputs, not wall-clock ZIP metadata.
    story_spine.portable_bundle(output, resource, {}, {},
        {'asset_version': '3.7.1', 'download_id': 'pinned-client'}, movie_fragments=result['files'])
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest


@pytest.mark.asyncio
async def test_build_stage_wires_extracted_fragments_into_bundle(tmp_path, monkeypatch):
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    from wuwa_story_worker import sequence_controls

    config = tmp_path / 'config' / 'files'
    config.mkdir(parents=True)
    (config / 'db_SpineBackground.db').touch()
    monkeypatch.setattr(story_spine, 'export_assets', AsyncMock(return_value=config.parent / 'manifest.json'))
    monkeypatch.setattr(story_spine, 'prefab_images', AsyncMock(return_value=([], [])))
    monkeypatch.setattr(story_spine, 'sequence_prefabs', lambda *_: ([], []))
    monkeypatch.setattr(story_spine, 'build_icons', AsyncMock(return_value={}))
    monkeypatch.setattr(sequence_controls, 'sequence_control_score', lambda *_: {'media': score(), 'events': [], 'preloads': []})
    result = {'media': {'sections': [], 'complete_video': False}, 'files': {'movie': tmp_path / 'clip.mp4'}, 'missing': []}
    prepare = AsyncMock(return_value=result)
    monkeypatch.setattr(sequence_fragments, 'prepare_sequence_fragments', prepare)
    bundle = object()
    packed = []

    def pack(*args, **kwargs):
        packed.append((args, kwargs))
        return bundle, []

    monkeypatch.setattr(story_spine, 'portable_bundle', pack)
    (tmp_path / 'plan.json').write_text(json.dumps({'version': '3.7.1', 'id': 'client', 'tier': 'hd'}))
    presentation = flow_presentation({'StateKey': 'Story_28_1', 'Actions': [
        {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': '/Game/Aki/Sequence/DA.DA'}}]})
    assert await story_spine.build_spine_bundle(tmp_path, None, tmp_path / 'tool', tmp_path / 'tool',
                                               presentation=presentation) == (bundle, [])
    prepare.assert_awaited_once()
    assert packed[0][1]['movie_fragments'] == result['files']
    assert packed[0][0][1]['control_score']['media'] == result['media']


@pytest.mark.asyncio
async def test_individual_publication_preserves_sections_and_reuses_release_scoped_references(tmp_path, fragment_client):
    from sqlalchemy.dialects import postgresql

    result = await sequence_fragments.prepare_sequence_fragments(tmp_path, score(), tmp_path / 'tool')
    resource = {'resource_id': None, 'controller_archive': True, 'variants': [], 'control_score': {'media': result['media']}}
    bundle, _ = story_spine.portable_bundle(tmp_path / 'scene.zip', resource, {}, {},
        {'asset_version': '3.7.1', 'download_id': 'client'}, movie_fragments=result['files'])
    target = {'owner': 4648369, 'release_id': 21, 'path': 'story_sequence:flow_state:Story_28_1'}
    provenance = {'asset_version': '3.7.1', 'download_id': 'client', 'source_files': [{'sha256': 'f' * 64}]}
    references, blobs = {}, {}

    async def lookup(statement):
        if 'storage.file_type' in str(statement):
            return 'unknown'  # Existing raw blob type must remain compatible.
        compiled = statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True})
        assert "reference_type = 'story_sequence_fragment'" in str(compiled)
        assert 'owner_node_id = 4648369' in str(compiled)
        assert "'3.7.1'" in str(compiled) and "'client'" in str(compiled)
        return references.get(str(compiled))

    session = SimpleNamespace(scalar=AsyncMock(side_effect=lookup))

    async def register(db, path, kind, **kwargs):
        assert kind == 'unknown' and kwargs['mime_type'] == 'video/mp4'
        digest = hashlib.sha256(path.read_bytes()).digest()
        return blobs.setdefault(digest, SimpleNamespace(id=len(blobs) + 100, sha256=digest))

    async def register_reference(db, **kwargs):
        # Store against the actual lookup statement so replay exercises its full scope.
        compiled = session.scalar.await_args.args[0].compile(dialect=postgresql.dialect(),
                                                             compile_kwargs={'literal_binds': True})
        reference = SimpleNamespace(**kwargs, metadata_json={})
        references[str(compiled)] = reference
        return reference

    service = SimpleNamespace(register_file=AsyncMock(side_effect=register),
                              register_variant=AsyncMock(), register_reference=AsyncMock(side_effect=register_reference))
    for release in (21, 21, 15):
        target['release_id'] = release
        published = await sequence_fragments.publish_sequence_fragments(
            session, bundle, target, SimpleNamespace(id=55), service, 80, provenance)
        assert published['complete_video'] is False and published['schema_version'] == 1
        assert published['game_source'] == provenance
        assert len(published['components']) == 2 and len(published['sections']) == 3
        for old, section in zip(result['media']['sections'], published['sections'], strict=True):
            assert all(section[key] == old[key] for key in ('source', 'reference', 'clock', 'range', 'parameters'))
            assert section['status'] == 'published' and section['mime_type'] == 'video/mp4'
            assert section['object_key'].startswith('objects/') and section['size_bytes'] > 0
            assert section['file_id'] in [file.id for file in blobs.values()]
        assert len(references) == (2 if release == 21 else 4)
    assert len(blobs) == 2 and service.register_reference.await_count == 4
    assert all(reference.metadata_json['game_source'] == provenance for reference in references.values())
    assert all(reference.metadata_json['bundle_file_id'] == 55 for reference in references.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['hash', 'size', 'provenance'])
async def test_unverified_fragment_publication_fails_before_registering_files(tmp_path, fragment_client, corruption):
    result = await sequence_fragments.prepare_sequence_fragments(tmp_path, score(), tmp_path / 'tool')
    resource = {'resource_id': None, 'controller_archive': True, 'variants': [], 'control_score': {'media': result['media']}}
    original, _ = story_spine.portable_bundle(tmp_path / 'original.zip', resource, {}, {},
        {'asset_version': '3.7.1', 'download_id': 'client'}, movie_fragments=result['files'])
    with zipfile.ZipFile(original) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries['manifest.json'])
    entry = result['media']['sections'][0]['file']
    if corruption == 'hash':
        entries[entry] = b'x' * len(entries[entry])
    elif corruption == 'size':
        manifest['files'][entry]['size_bytes'] -= 1
    else:
        manifest['download_id'] = 'different-client'
    entries['manifest.json'] = json.dumps(manifest).encode()
    bundle = tmp_path / 'corrupt.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    service = SimpleNamespace(register_file=AsyncMock())
    with pytest.raises(ValueError):
        await sequence_fragments.publish_sequence_fragments(SimpleNamespace(), bundle, {}, None, service, 80,
            {'asset_version': '3.7.1', 'download_id': 'client'})
    service.register_file.assert_not_awaited()
