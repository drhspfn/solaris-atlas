import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from wuwa_story_worker.sequence_fragments import publish_sequence_fragments
from wuwa_story_worker.sequence_media import sequence_media_score


def image_bytes():
    output = io.BytesIO()
    Image.new('RGB', (2, 2)).save(output, format='PNG')
    return output.getvalue()


def archive(tmp_path, state='ready', entry='composition/frame.png', corrupt=False):
    png = image_bytes()
    entries = {'controls.json': json.dumps(
        {'media': {'sections': []}}).encode(), entry: png}
    manifest = {'asset_version': '3.7.1', 'download_id': 'client',
                'files': {key: {'sha256': hashlib.sha256(value).hexdigest(), 'size_bytes': len(value)}
                          for key, value in entries.items()},
                'composition': {'schema_version': 1, 'state': state, 'reasons': [{'code': 'example'}],
                                'frames': [{'file': entry, 'source': 'shot', 'scene_source': 'scene', 'time_seconds': 1.5}]}}
    if corrupt:
        entries[entry] = b'X' * len(png)
    path = tmp_path / 'bundle.zip'
    with zipfile.ZipFile(path, 'w') as out:
        for key, value in entries.items():
            out.writestr(key, value)
        out.writestr('manifest.json', json.dumps(manifest))
    return path


@pytest.mark.asyncio
async def test_publishes_composition_with_exact_bundle_provenance(tmp_path):
    reference = SimpleNamespace()
    data = image_bytes()
    service = SimpleNamespace(register_file=AsyncMock(return_value=SimpleNamespace(id=91, sha256=hashlib.sha256(data).digest())),
                              register_variant=AsyncMock(), register_reference=AsyncMock(return_value=reference))
    proof = {'asset_version': '3.7.1', 'download_id': 'client'}
    result = await publish_sequence_fragments(SimpleNamespace(scalar=AsyncMock(return_value=None)), archive(tmp_path),
                                              {'owner': 7, 'release_id': 21, 'path': 'flow'}, SimpleNamespace(id=90), service, 1, proof)
    assert result['composition']['state'] == 'ready'
    assert result['sections'] == [{'kind': 'illustration', 'status': 'published', 'file_id': 91,
                                   'file': 'composition/frame.png', 'source': 'shot', 'scene_source': 'scene', 'time_seconds': 1.5}]
    assert reference.metadata_json['bundle_file_id'] == 90
    assert reference.metadata_json['game_source'] == proof
    assert reference.metadata_json['frames'] == [
        {'source': 'shot', 'scene_source': 'scene', 'time_seconds': 1.5}]
    assert service.register_reference.await_args.kwargs['release_id'] == 21


@pytest.mark.asyncio
@pytest.mark.parametrize('entry,corrupt', [('composition/../bad.png', False), ('composition/frame.png', True)])
async def test_invalid_frames_are_not_published(tmp_path, entry, corrupt):
    service = SimpleNamespace(register_file=AsyncMock())
    with pytest.raises(ValueError):
        await publish_sequence_fragments(None, archive(tmp_path, entry=entry, corrupt=corrupt), {}, None, service, 1,
                                         {'asset_version': '3.7.1', 'download_id': 'client'})
    service.register_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['unsupported', 'failed'])
async def test_unavailable_composition_retains_reasons_without_publishing(tmp_path, state):
    service = SimpleNamespace(register_file=AsyncMock())
    result = await publish_sequence_fragments(None, archive(tmp_path, state=state), {}, None, service, 1,
                                              {'asset_version': '3.7.1', 'download_id': 'client'})
    assert result['composition'] == {
        'schema_version': 1, 'state': state, 'reasons': [{'code': 'example'}]}
    assert result['sections'] == []
    service.register_file.assert_not_awaited()


def test_sequence_assets_follow_explicit_da_and_subsequence_ownership():
    values = [
        {'Type': 'BP_SequenceData_C', 'Name': 'DA', 'Package': '/Game/DA', '_export_index': 0,
         'Properties': {'剧情资源': [{'ObjectPath': '/Game/Root'}]}},
        {'Type': 'LevelSequence', 'Package': '/Game/Root', '_export_index': 0},
        {'Type': 'MovieScene', 'Package': '/Game/Root', '_export_index': 1,
         'Outer': {'ObjectPath': '/Game/Root.0'}},
        {'Type': 'MovieSceneTrack', 'Package': '/Game/Root', '_export_index': 2,
         'Outer': {'ObjectPath': '/Game/Root.1'}},
        {'Type': 'MovieSceneSubSection', 'Package': '/Game/Root', '_export_index': 3,
         'Outer': {'ObjectPath': '/Game/Root.2'}, 'Properties': {'SubSequence': {'ObjectPath': '/Game/Child'}}},
        {'Type': 'LevelSequence', 'Package': '/Game/Child', '_export_index': 0},
        {'Type': 'MovieScene', 'Package': '/Game/Child', '_export_index': 1,
         'Outer': {'ObjectPath': '/Game/Child.0'}},
        {'Type': 'LevelSequence', 'Package': '/Game/Neighbor', '_export_index': 0},
        {'Type': 'MovieScene', 'Package': '/Game/Neighbor', '_export_index': 1,
         'Outer': {'ObjectPath': '/Game/Neighbor.0'}},
    ]
    assert sequence_media_score(values)['sequence_assets'] == [
        {'asset': '/Game/DA.DA', 'scenes': ['/Game/Child.1', '/Game/Root.1']}]
    values.append({'Type': 'LevelSequence', 'Package': '/Game/Root', '_export_index': 4})
    assert sequence_media_score(values)['sequence_assets'] == []
