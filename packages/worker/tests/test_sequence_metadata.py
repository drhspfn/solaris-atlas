import hashlib
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.ingestion.spine_presentation import flow_presentation

from wuwa_story_worker import entity_media, sequence_metadata_jobs
from wuwa_story_worker.sequence_metadata import recompile_bundle


def retained(tmp_path):
    presentation = flow_presentation({'StateKey': 'Story_1', 'Actions': [
        {'Name': 'PlaySequenceData', 'Params': {'Path': '/Game/Aki/Scene.Scene'}}]})
    target = {'owner': 7, 'release_id': 21, 'path': 'story_sequence:flow_state:Story_1',
              'resource_id': None, 'kind': 'spine', 'language': None, 'reference_type': 'story_presentation',
              'spine_recipe': 4, 'sequence_media_schema': 1, 'sequence_metadata_revision': 2,
              'presentation_sha256': presentation['sha256'], 'presentation': presentation}
    exports = [
        {'Type': 'MovieScene', 'Properties': {'TickResolution': {'Numerator': 30}}},
        {'Type': 'MovieSceneMediaTrack', 'Outer': {'ObjectPath': '/Game/Aki/Scene.0'}},
        {'Type': 'MovieSceneMediaSection', 'Outer': {'ObjectPath': '/Game/Aki/Scene.1'},
         'Properties': {'MediaSource': {'ObjectPath': '/Game/Clip'}, 'StartFrameOffset': {'Value': 3}}},
        {'Type': 'MovieSceneSkeletalAnimationTrack', 'Outer': {'ObjectPath': '/Game/Aki/Scene.0'}},
        {'Type': 'MovieSceneSkeletalAnimationSection', 'Outer': {'ObjectPath': '/Game/Aki/Scene.3'},
         'Properties': {'Params': {'Animation': {'ObjectPath': '/Game/Attack'}}}},
    ]
    movie = 'media/Client/Content/Aki/Movies/clip.mp4'
    controls = {'unresolved': [{'code': 'missing_source', 'source': '/Game/Absent'}], 'media': {'sections': [{
        'kind': 'movie_fragment', 'source': '/Game/Aki/Scene.2', 'scene_source': '/Game/Aki/Scene.0',
        'reference': '/Game/Clip', 'clock': {'numerator': 30, 'denominator': 1}, 'range': None,
        'parameters': {'StartFrameOffset': {'Value': 3}}, 'file': movie,
        'archive_path': movie.removeprefix('media/'), 'status': 'extracted'}]}}
    entries = {'presentation.json': json.dumps(presentation).encode(), 'controls.json': json.dumps(controls).encode(),
               'decoded/files/Client/Content/Aki/Scene.json': json.dumps(exports).encode(),
               'source/Client/Content/Aki/Scene.uasset': b'exact source', movie: b'exact movie bytes'}
    missing = [{'status': 'not_in_client', 'expected': '/Game/Absent'}]
    manifest = {'schema_version': 4, 'kind': 'story_spine', 'asset_version': '3.7.1', 'download_id': 'a' * 64,
                'control_sources': {'missing': missing}, 'presentation': {'file': 'presentation.json', 'sha256': presentation['sha256']},
                'controls': {'file': 'controls.json'}, 'files': {name: {'size_bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest()} for name, data in entries.items()}}
    path = tmp_path / 'old.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest))
    return path, target, entries, missing


def test_recompile_preserves_incomplete_evidence_movie_bytes_and_manifest_version(tmp_path):
    path, target, original, missing = retained(tmp_path)
    output, originals = recompile_bundle(path, tmp_path / 'new' / 'new.zip', target)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        controls = json.loads(archive.read('controls.json'))
        assert manifest['schema_version'] == 4 and manifest['sequence_metadata_revision'] == 2
        assert all(item in manifest['control_sources']['missing'] for item in missing)
        assert {'code': 'missing_source', 'source': '/Game/Absent'} in controls['unresolved']
        assert controls['media']['complete_video'] is False
        assert any(s['kind'] == 'skeletal_animation' for s in controls['media']['sections'])
        movie, = [s for s in controls['media']['sections'] if s['kind'] == 'movie_fragment']
        assert movie['status'] == 'extracted' and movie['parameters']['StartFrameOffset'] == {'Value': 3}
        assert archive.read(movie['file']) == original[movie['file']]
        assert manifest['files'][movie['file']]['sha256'] == hashlib.sha256(original[movie['file']]).hexdigest()
    assert {p.suffix for p in originals} == {'.uasset', '.mp4'}


def test_corrupt_retained_json_or_changed_presentation_is_never_registered(tmp_path):
    path, target, _, _ = retained(tmp_path)
    target['presentation_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='presentation differs'):
        recompile_bundle(path, tmp_path / 'new.zip', target)


def test_retained_entry_checksum_mismatch_refuses_recompile(tmp_path):
    path, target, _, _ = retained(tmp_path)
    with zipfile.ZipFile(path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    name = 'source/Client/Content/Aki/Scene.uasset'
    entries[name] = b'wrong source'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match='checksum differs'):
        recompile_bundle(path, tmp_path / 'new.zip', target)


@pytest.mark.asyncio
@pytest.mark.parametrize('revision', [None, 2, 3])
async def test_retained_lookup_downloads_only_stale_exact_source_revision(tmp_path, revision):
    from wuwa_story_worker.sequence_metadata import retained_bundle

    path, target, _, _ = retained(tmp_path)
    proof = {'schema_version': 1, 'package_path': target['path'], 'asset_version': '3.7.1',
             'download_id': 'a' * 64, 'spine_recipe': 4, 'presentation_sha256': target['presentation_sha256'],
             'sequence_media_schema': 1, 'sequence_metadata_revision': revision, 'control_sources_complete': False,
             'source_files': [{'package_path': 'Client/Content/Aki/Scene.uasset', 'sha256': 'a' * 64}]}
    session = AsyncMock()
    session.execute.return_value = [({'game_source': proof}, 'retained-object')]
    calls = []

    async def get(key):
        calls.append(key)
        yield path.read_bytes()

    storage = SimpleNamespace(backend='s3', bucket='media', get=get)
    result = await retained_bundle(session, storage, tmp_path / 'job', target,
                                   {'asset_version': '3.7.1', 'download_id': 'a' * 64})
    assert bool(result) is (revision is None)
    assert calls == (['retained-object'] if revision is None else [])


@pytest.mark.asyncio
async def test_normal_handler_retained_path_never_invokes_full_game_export(tmp_path, monkeypatch):
    path, target, _, _ = retained(tmp_path)
    full = AsyncMock(side_effect=AssertionError('must not export the game'))
    from wuwa_story_worker import story_spine

    monkeypatch.setattr(story_spine, 'build_spine_bundle', full)
    files, missing = await entity_media.build_entity_files(tmp_path, [target], None, None,
                                                          retained_sequences={target['path']: path})
    assert len(files) == 1 and not missing
    full.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['completed', 'running'])
async def test_revision_cli_uses_fresh_normal_targets_and_waits_active_source(tmp_path, monkeypatch, status):
    _, current, _, _ = retained(tmp_path)
    old = {key: value for key, value in current.items() if key != 'sequence_metadata_revision'}
    request = MediaRequest(download_id='a' * 64, asset_version='3.7.1', game_version='3.7.0',
                           release_id=21, entities=['flow_state:Story_1'], kinds=['image'])
    canonical = {'schema_version': 1, 'job_type': 'media.entities', 'request': request.model_dump(), 'targets': [old]}
    old_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).digest()
    run = SimpleNamespace(processor_id=1, input_hash=old_hash, status=status,
                          metadata_json={'payload': {**canonical, 'run_id': 100}})
    session = AsyncMock()
    session.get.side_effect = [run, SimpleNamespace(key='entity_media')]
    session.scalar.return_value = None
    monkeypatch.setattr(sequence_metadata_jobs, 'entity_media_targets', AsyncMock(return_value=[current]))
    enqueue = AsyncMock(return_value=SimpleNamespace(id=101, status='queued'))
    monkeypatch.setattr(sequence_metadata_jobs, 'enqueue_entity_media', enqueue)
    result = await sequence_metadata_jobs.enqueue_metadata_revision(session, 100, apply=True)
    assert result['current_input_hash'] != old_hash.hex()
    assert result['revisions'] == [{'owner': 7, 'sequence_metadata_revision': 2}]
    if status == 'running':
        assert result['action'] == 'wait_source_terminal'
        enqueue.assert_not_awaited()
    else:
        assert result['run_id'] == 101 and result['applied'] is True
        enqueue.assert_awaited_once_with(session, request)
