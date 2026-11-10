import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest

from wuwa_story_worker import sequence_composition as compositor


@pytest.mark.parametrize(('sizes', 'duplicate', 'reason'), [
    ([0] * 50001, False, 'entry count limit'),
    ([1, 1], True, 'duplicate entries'),
    ([256 * 1024 * 1024 + 1], False, 'per-entry size limit'),
    ([256 * 1024 * 1024] * 8 + [1], False, 'total uncompressed size limit'),
])
def test_archive_limit_failure_identifies_actual_cause(sizes, duplicate, reason):
    infos = [SimpleNamespace(filename='same' if duplicate else str(i), file_size=size)
             for i, size in enumerate(sizes)]
    with pytest.raises(ValueError, match=reason):
        compositor._validate_archive_limits(infos)


def test_larger_composition_archives_are_allowed():
    infos = [SimpleNamespace(filename=str(i), file_size=128 * 1024 * 1024) for i in range(8)]
    compositor._validate_archive_limits(infos)


def bundle(path):
    payload = b'{"events": []}'
    manifest = {'controls': {'file': 'controls.json'}, 'files': {'controls.json': {
        'sha256': hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload)}}}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('controls.json', payload)
    return path


def composition(path):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read('manifest.json')), archive.namelist()


def test_disabled_records_unsupported_without_rendering(tmp_path, monkeypatch):
    path = bundle(tmp_path / 'bundle.zip')
    monkeypatch.setattr(compositor, '_plans', lambda *_: [{}])
    monkeypatch.delenv('WUWA_RIGLYPH_ENABLED', raising=False)
    monkeypatch.setattr(compositor, '_render', lambda *_: pytest.fail('renderer invoked'))
    assert compositor.annotate_bundle(path) == path
    manifest, names = composition(path)
    assert manifest['composition']['state'] == 'unsupported'
    assert manifest['composition']['reasons'][0]['code'] == 'renderer_disabled'
    assert 'disabled' in manifest['composition']['reasons'][0]['detail']
    assert manifest['composition']['frames'] == [] and names.count('controls.json') == 1


def test_ready_frames_have_hashes_and_reannotation_replaces_them(tmp_path, monkeypatch):
    path = bundle(tmp_path / 'bundle.zip')
    monkeypatch.setenv('WUWA_RIGLYPH_ENABLED', '1')
    plan = {'source': 'event', 'scene_source': 'scene', 'time': 1.5, 'state_time': 0}
    monkeypatch.setattr(compositor, '_plans', lambda *_: [plan.copy()])
    def render(_bundle, _archive, _manifest, plans, *_args):
        plans[0]['file'] = 'composition/frame-000.png'
        return {'composition/frame-000.png': b'frame'}
    monkeypatch.setattr(compositor, '_render', render)
    compositor.annotate_bundle(path)
    compositor.annotate_bundle(path)
    manifest, names = composition(path)
    assert manifest['composition']['state'] == 'ready'
    assert manifest['composition']['frames'][0]['time_seconds'] == 0
    assert manifest['composition']['frames'][0]['sample_time_seconds'] == 1.5
    assert manifest['files']['composition/frame-000.png']['sha256'] == hashlib.sha256(b'frame').hexdigest()
    assert names.count('composition/frame-000.png') == 1


def test_render_failure_removes_all_partial_frames_and_preserves_source(tmp_path, monkeypatch):
    path = bundle(tmp_path / 'bundle.zip')
    monkeypatch.setenv('WUWA_RIGLYPH_ENABLED', 'true')
    monkeypatch.setattr(compositor, '_plans', lambda *_: [{}])
    def fail(*_):
        raise ValueError('conversion failed after first frame')
    monkeypatch.setattr(compositor, '_render', fail)
    compositor.annotate_bundle(path)
    manifest, names = composition(path)
    assert manifest['composition']['state'] == 'failed'
    assert manifest['composition']['frames'] == []
    assert manifest['composition']['reasons'][0]['code'] == 'render_failed'
    assert not any(name.startswith('composition/') for name in names)
    with zipfile.ZipFile(path) as archive:
        assert archive.read('controls.json') == b'{"events": []}'


def test_duplicate_entries_rejected_before_read_and_original_unchanged(tmp_path):
    path = bundle(tmp_path / 'bundle.zip')
    with pytest.warns(UserWarning), zipfile.ZipFile(path, 'a') as archive:
        archive.writestr('controls.json', 'duplicate')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='duplicate'):
        compositor.annotate_bundle(path)
    assert path.read_bytes() == before


def test_verified_read_rejects_declared_size_before_decompression(tmp_path):
    path = bundle(tmp_path / 'bundle.zip')
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        manifest['files']['controls.json']['size_bytes'] = 1
        with pytest.raises(compositor.UnsupportedComposition, match='size'):
            compositor._read(archive, manifest, 'controls.json')

def test_unbound_event_requires_verified_empty_function(monkeypatch):
    event = {'source': '/Game/Scene.2', 'key_index': 0, 'frame': 7, 'function': None}
    section = {'Type': 'MovieSceneEventTriggerSection', 'Properties': {'EventChannel': {
        'KeyTimes': [{'Value': 7}], 'KeyValues': [{'Ptrs': {'Function': None,
            'BoundObjectProperty': {'Path': [], 'ResolvedOwner': None}}}]}}}
    monkeypatch.setattr(compositor, '_exports', lambda *_: [{}, {}, section])
    assert compositor._empty_event(None, {}, event)
    section['Properties']['EventChannel']['KeyValues'][0]['Ptrs']['Function'] = {'ObjectPath': 'unknown'}
    assert not compositor._empty_event(None, {}, event)


def test_unknown_call_is_not_ignored(monkeypatch):
    controls = {'events': [
        {'time_seconds': 0, 'source': 'source', 'scene_source': 'scene',
         'calls': [{'function': 'OpenUiView'}]},
        {'time_seconds': 1, 'source': 'other', 'scene_source': 'scene',
         'calls': [{'function': 'ChangeUiOpacity'}]},
        {'time_seconds': 2, 'source': 'source', 'scene_source': 'scene',
         'calls': [{'function': 'CloseUiView'}]},
    ]}
    monkeypatch.setattr(compositor, '_read', lambda *_: json.dumps(controls).encode())
    with pytest.raises(compositor.UnsupportedComposition, match='Unknown executable'):
        compositor._plans(None, {'controls': {'file': 'controls.json'}})

def test_validated_black_sprite_is_an_ordered_solid_layer():
    layout = {'root': 1, 'size': [2560, 1440], 'assets': [], 'actors': {
        1: {'id': 1, 'parent': 0, 'index': 0, 'active': True, 'kind': "Class'UIItem'", 'props': {}, 'components': []},
        2: {'id': 2, 'parent': 1, 'index': 0, 'active': True, 'kind': "Class'UISprite'",
            'props': {'Color': [0, 0, 0, 255], 'sprite': {'ObjectPath': '/Game/Aki/UI/UIResources/Common/Atlas/SP_CommonDefault'}}, 'components': []},
        3: {'id': 3, 'parent': 1, 'index': 1, 'active': True, 'kind': "Class'UISpineRenderable'",
            'props': {'CurrSkeletonData': {'ObjectPath': '/Game/Rig'}},
            'components': [{'SkeletonData': {'ObjectPath': '/Game/Rig'}, 'Atlas': {'ObjectPath': '/Game/Atlas'}}]},
    }}
    layers = compositor.visible_layers(layout)
    assert layers[0] == {'kind': 'solid', 'color': [0, 0, 0], 'opacity': 1, 'actor': 2}
    assert layers[1]['source'] == '/Game/Rig'
    layout['actors'][2]['index'] = 2
    with pytest.raises(ValueError, match='Foreground'):
        compositor.visible_layers(layout)
