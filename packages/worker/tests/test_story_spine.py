import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker.event_resources import ui_references
from wuwa_story_worker.story_spine import portable_bundle


@pytest.mark.asyncio
async def test_open_components_are_published_individually_with_archive_provenance(tmp_path):
    from wuwa_story_worker.story_spine import publish_open_playback
    payloads = {'scene.json': b'{"format":"solaris.skeletal-scene.v1"}', 'page.png': b'png fixture'}
    playback = {'variants': [{'rover': None, 'animation': 'idle', 'scene': 'scene.json', 'textures': {'page.png': 'page.png'}}], 'unsupported': []}
    manifest = {'playback': playback, 'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)} for name, data in payloads.items()}}
    bundle = tmp_path / 'scene.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        for name, data in payloads.items():
            archive.writestr(name, data)
    async def register(session, path, kind, **kwargs):
        return SimpleNamespace(id=100, sha256=hashlib.sha256(path.read_bytes()).digest())
    service = SimpleNamespace(register_file=AsyncMock(side_effect=register), register_variant=AsyncMock(), register_reference=AsyncMock(side_effect=lambda *_args, **_kwargs: SimpleNamespace()))
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    target = {'owner': 5, 'release_id': 7, 'path': 'spine_background:7'}
    result = await publish_open_playback(session, bundle, target, SimpleNamespace(id=55), service, SimpleNamespace(public_url=lambda key: 'https://cdn.test/' + key), 80, '3.7.0')
    assert [call.args[2] for call in service.register_file.await_args_list] == ['unknown', 'image']
    assert len(result['components']) == 2
    assert result['variants'][0]['scene_url'].startswith('https://cdn.test/objects/')
    assert result['variants'][0]['textures']['page.png'] != result['variants'][0]['scene_url']
    assert service.register_reference.await_count == 2
    assert all(call.kwargs['reference_type'] == 'story_spine_part' for call in service.register_reference.await_args_list)
    # Re-running publication updates existing references rather than creating duplicates.
    references = [SimpleNamespace(), SimpleNamespace()]
    session.scalar = AsyncMock(side_effect=references)
    service.register_reference.reset_mock()
    await publish_open_playback(session, bundle, target, SimpleNamespace(id=55), service, SimpleNamespace(public_url=lambda key: 'https://cdn.test/' + key), 80, '3.7.0')
    service.register_reference.assert_not_awaited()
    assert all(reference.metadata_json['bundle_file_id'] == 55 for reference in references)


def test_typed_spine_atlas_pages_follow_full_client_package_paths():
    value = {'ObjectName': "Texture2D'Bg'",
             'ObjectPath': 'Client/Content/Aki/UI/Image/Bg.1'}
    assert list(ui_references(value)) == [('image', '/Game/Aki/UI/Image/Bg')]
    assert list(ui_references({'ObjectName': "Texture2D'Bg'",
                'ObjectPath': 'Client/Content/../../secret.0'})) == []


def fixture(tmp_path):
    raw = tmp_path / 'raw'
    package = 'Client/Content/Aki/UI/Spine/Story.uasset'
    path = raw / package
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original cooked skeleton package')
    path.with_suffix('.uexp').write_bytes(b'original export bytes')
    decoded = tmp_path / 'decoded'
    decoded.mkdir()
    exports = [{'Type': 'SpineAtlasAsset', 'Properties': {'atlasFileName': 'C:/artist/Story.atlas',
                                                          'rawData': 'Story.png\nsize: 1, 1\n', 'atlasPages': [
                                                              {'ObjectName': "Texture2D'Story'", 'ObjectPath': 'Client/Content/Aki/UI/Textures/Story.1'}]}},
               {'Type': 'SpineSkeletonDataAsset', 'Properties': {
                   'skeletonDataFileName': 'C:/artist/Story.skel', 'rawData': [1, 2, 3, 255]}}]
    (decoded / 'Story.json').write_text(json.dumps(exports), encoding='utf-8')
    image = tmp_path / 'Story.png'
    image.write_bytes(b'converted png')
    assets = {'/Game/Aki/UI/Spine/Story': {'raw': raw,
                                           'decoded': decoded, 'package': package}}
    icons = {
        '/Game/Aki/UI/Textures/Story': {'path': str(image), 'raw_paths': []}}
    return assets, icons


def test_spine_archive_contains_portable_data_originals_and_verified_manifest(tmp_path):
    assets, icons = fixture(tmp_path)
    output, originals = portable_bundle(tmp_path / 'bundle.zip', {'resource_id': 7, 'variants': []},
                                        assets, icons, {'asset_version': '3.7.0', 'download_id': 'client', 'tier': 'hd'})
    digest = hashlib.sha256(output.read_bytes()).digest()
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['resource_id'] == 7
        assert any(path.endswith('/Story.skel') for path in manifest['files'])
        assert len(originals) == 2
        for path, info in manifest['files'].items():
            assert not Path(path).is_absolute(
            ) and '..' not in Path(path).parts
            assert hashlib.sha256(archive.read(
                path)).hexdigest() == info['sha256']
    portable_bundle(output, {'resource_id': 7, 'variants': []}, assets, icons,
                    {'asset_version': '3.7.0', 'download_id': 'client', 'tier': 'hd'})
    assert hashlib.sha256(output.read_bytes()).digest() == digest
    with pytest.raises(ValueError, match='atlas page'):
        portable_bundle(tmp_path / 'incomplete.zip', {}, assets, {}, {})
    assert not (tmp_path / 'incomplete.zip').exists()


def test_archive_retains_score_and_rejects_changed_control_source(tmp_path):
    from wuwa_story.ingestion.spine_presentation import flow_presentation
    assets, icons = fixture(tmp_path)
    presentation = flow_presentation({'StateKey': 'branch', 'Actions': [
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [
            {'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}}]}},
        {'Name': 'PlaySpine', 'Params': {'Config': {'Type': 'Play', 'Name': '1to2'}}}]})
    resource = {'resource_id': 7, 'variants': []}
    output = tmp_path / 'score.zip'
    portable_bundle(output, resource, assets, icons,
                    {}, presentation=presentation)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['schema_version'] == 4
        assert json.loads(archive.read(
            manifest['presentation']['file'])) == presentation
        assert manifest['presentation']['timing_status'] == 'unresolved'
        assert 'presentation.json' in manifest['files']
    digest = hashlib.sha256(output.read_bytes()).digest()
    presentation['source']['StateKey'] = 'different'
    with pytest.raises(ValueError, match='source hash'):
        portable_bundle(output, resource, assets, icons,
                        {}, presentation=presentation)
    assert hashlib.sha256(output.read_bytes()).digest() == digest


def test_score_cannot_be_attached_to_an_unrelated_resource(tmp_path):
    from wuwa_story.ingestion.spine_presentation import flow_presentation
    assets, icons = fixture(tmp_path)
    with pytest.raises(ValueError, match='does not reference'):
        portable_bundle(tmp_path / 'bad.zip', {'resource_id': 8, 'variants': []}, assets, icons, {},
                        presentation=flow_presentation({'Actions': []}))


def test_controller_only_archive_does_not_invent_a_skeleton_or_playback(tmp_path):
    from wuwa_story.ingestion.spine_presentation import flow_presentation
    assets, _ = fixture(tmp_path)
    (tmp_path / 'decoded/Story.json').write_text(json.dumps([{'Type': 'LevelSequence', 'Properties': {}}]))
    resource = {'resource_id': None, 'controller_archive': True, 'variants': [],
                'control_score': {'format': 'solaris.sequence-controls.v1', 'events': [], 'dialogues': []}}
    output, _ = portable_bundle(tmp_path / 'controller.zip', resource, assets, {}, {},
                               presentation=flow_presentation({'Actions': [{'Name': 'PlaySequenceData',
                                                        'Params': {'Path': '/Game/Story.Sequence'}}]}))
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['skeletons'] == [] and manifest['playback']['variants'] == []
        assert json.loads(archive.read(manifest['controls']['file'])) == resource['control_score']


@pytest.mark.asyncio
async def test_controller_exports_and_flow_scores_survive_as_separate_archive_outputs(tmp_path, monkeypatch):
    from wuwa_story.ingestion.spine_presentation import flow_presentation

    from wuwa_story_worker import story_spine

    assets, icons = fixture(tmp_path)
    config = tmp_path / 'config' / 'files'
    config.mkdir(parents=True)
    (config / 'db_SpineBackground.db').touch()
    monkeypatch.setattr(story_spine, 'export_assets', AsyncMock(
        return_value=config.parent / 'manifest.json'))
    monkeypatch.setattr(story_spine, 'background_resource', lambda *_: {'resource_id': 7,
                        'variants': [{'prefab': '/Game/Story', 'animation': 'idle', 'rover': None}]})
    control = tmp_path / 'control'
    control.mkdir()
    (control / 'Client').mkdir()
    (control / 'Client' /
     'Controller.uasset').write_bytes(b'cooked control with unsupported class')
    decoded = control / 'decoded'
    decoded.mkdir()
    (decoded / 'incomplete.json').write_text('{broken')

    async def extract(root, paths, *, assets, **kwargs):
        if not kwargs:
            assets.update(fixture_assets)
            return [], []
        assert paths == ['/Game/Controller'] and kwargs['include_sequences']
        assets['/Game/Controller'] = {'raw': control, 'decoded': decoded,
                                      'package': 'Client/Controller.uasset', 'decode_failed': True}
        return [], [{'expected': '/Game/Controller', 'status': 'dependency_failed'}]
    fixture_assets = assets
    monkeypatch.setattr(story_spine, 'prefab_images', extract)
    monkeypatch.setattr(story_spine, 'build_icons',
                        AsyncMock(return_value=icons))
    (tmp_path / 'plan.json').write_text(json.dumps(
        {'version': '3.7.0', 'id': 'client', 'tier': 'hd'}))
    outputs = []
    for state in ('first', 'second'):
        presentation = flow_presentation({'StateKey': state, 'Actions': [
            {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': '/Game/Controller.Controller',
                                            'TalkItems': [{'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}}]}}]})
        output, _ = await story_spine.build_spine_bundle(tmp_path, 7, tmp_path / 'tool', tmp_path / 'tool',
                                                         presentation=presentation)
        outputs.append(output)
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['control_sources']['missing'][0]['expected'] == '/Game/Controller'
            assert archive.read(
                'source/Client/Controller.uasset').startswith(b'cooked control')
            assert json.loads(archive.read('presentation.json'))[
                'source']['StateKey'] == state
    assert outputs[0] != outputs[1] and all(
        output.exists() for output in outputs)


@pytest.mark.asyncio
async def test_missing_spine_does_not_drop_successful_static_images(tmp_path, monkeypatch):
    from PIL import Image

    from wuwa_story_worker import entity_media, story_spine
    image = tmp_path / 'static.png'
    Image.new('RGB', (2, 2)).save(image)
    monkeypatch.setattr(entity_media, 'build_icons', AsyncMock(return_value={
        '/Game/Image': {'path': str(image), 'raw_paths': []}}))
    monkeypatch.setattr(story_spine, 'build_spine_bundle', AsyncMock(
        side_effect=ValueError('Missing dependency')))
    targets = [{'kind': 'image', 'path': '/Game/Image'},
               {'kind': 'spine', 'path': 'spine_background:7', 'resource_id': 7}]
    files, missing = await entity_media.build_entity_files(tmp_path, targets, tmp_path / 'tool', tmp_path / 'tool')
    assert len(files) == 1 and files[0][0] == targets[0]
    assert missing == ['spine_background:7']
