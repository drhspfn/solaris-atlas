import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker.event_resources import ui_references
from wuwa_story_worker.story_spine import portable_bundle


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
