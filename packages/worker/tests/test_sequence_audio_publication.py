import hashlib
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker.sequence_fragments import publish_sequence_fragments


def audio_bundle(tmp_path, *, corrupt=False, entry='audio/event/en/shared.ogg'):
    track = {'language': 'en', 'rover': None, 'file': entry, 'status': 'rendered'}
    section = {'kind': 'audio_event', 'source': '/Game/M0334_All.407',
               'reference': '/Game/Aki/WwiseAudio/Events/play_all_sound_m0334',
               'clock': {'numerator': 30, 'denominator': 1}, 'range': {'authored': True},
               'sync_audio': True, 'tracks': [track]}
    entries = {'controls.json': json.dumps({'media': {'sections': [section]}}).encode(),
               entry: b'OggS-source-backed-render'}
    manifest = {'asset_version': '3.7.1', 'download_id': 'client',
                'files': {name: {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in entries.items()}}
    if corrupt:
        entries[entry] = b'x' * len(entries[entry])
    path = tmp_path / 'scene.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest))
    return path, section


@pytest.mark.asyncio
async def test_scene_soundtrack_publication_keeps_exact_owner_bundle_and_event(tmp_path):
    bundle, authored = audio_bundle(tmp_path)
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    reference = SimpleNamespace()
    service = SimpleNamespace(register_file=AsyncMock(return_value=SimpleNamespace(id=91)),
                              register_variant=AsyncMock(), register_reference=AsyncMock(return_value=reference))
    target = {'owner': 7, 'release_id': 21, 'path': 'story_sequence:flow_state:Flow28'}
    proof = {'asset_version': '3.7.1', 'download_id': 'client', 'sequence_metadata_revision': 3}
    result = await publish_sequence_fragments(session, bundle, target, SimpleNamespace(id=90), service, 100, proof)
    section, = result['sections']
    assert section['range'] == authored['range'] and section['sync_audio'] is True
    assert section['tracks'][0]['status'] == 'published' and section['tracks'][0]['file_id'] == 91
    assert result['complete_video'] is False
    assert reference.metadata_json['bundle_file_id'] == 90
    assert reference.metadata_json['section_source'] == '/Game/M0334_All.407'
    assert reference.metadata_json['event_reference'] == authored['reference']
    assert reference.metadata_json['game_source'] == proof
    assert service.register_reference.await_args.kwargs['owner_node_id'] == 7
    assert service.register_reference.await_args.kwargs['release_id'] == 21
    assert service.register_reference.await_args.kwargs['reference_type'] == 'story_sequence_audio'


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt,entry', [(True, 'audio/event/en/shared.ogg'),
                                         (False, 'audio/../outside.ogg'), (False, 'audio/movie.mp4')])
async def test_unverified_or_unsafe_scene_audio_never_publishes(tmp_path, corrupt, entry):
    bundle, _ = audio_bundle(tmp_path, corrupt=corrupt, entry=entry)
    service = SimpleNamespace(register_file=AsyncMock())
    with pytest.raises(ValueError):
        await publish_sequence_fragments(None, bundle, {}, None, service, 100,
                                        {'asset_version': '3.7.1', 'download_id': 'client'})
    service.register_file.assert_not_awaited()
