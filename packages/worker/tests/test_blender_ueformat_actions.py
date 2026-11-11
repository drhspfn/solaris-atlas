import json
import struct
from types import SimpleNamespace

import pytest

from wuwa_story_worker.blender_ueformat_actions import (
    filtered_glb,
    load_compatibility_check,
    load_manifest,
    preflight,
    read_glb,
)


def make_rig(offset):
    matrix = [[float(i == j) for j in range(4)] for i in range(4)]
    bone_matrix = [row[:] for row in matrix]
    bone_matrix[0][3] = offset
    bone = SimpleNamespace(name='root', parent=None, matrix_local=bone_matrix)
    class Bones(dict):
        def __iter__(self):
            return iter(self.values())
    return SimpleNamespace(data=SimpleNamespace(bones=Bones(root=bone)), matrix_world=matrix)


def test_small_ueformat_gltf_rounding_is_within_explicit_tolerance():
    assert load_compatibility_check()(make_rig(0), make_rig(1.4e-5), tolerance=2e-5) is None


def test_larger_rest_pose_difference_is_rejected():
    assert 'rest pose' in load_compatibility_check()(make_rig(0), make_rig(2.1e-5), tolerance=2e-5)

def make_glb(path, names):
    payload = json.dumps({'asset': {'version': '2.0'}, 'animations': [{'name': n} for n in names]}).encode()
    payload += b' ' * (-len(payload) % 4)
    binary = struct.pack('<I4s', 4, b'BIN\0') + b'1234'
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 20 + len(payload) + len(binary)) + struct.pack('<I4s', len(payload), b'JSON') + payload + binary)
    return path


def test_manifest_filters_pack_without_touching_binary(tmp_path):
    path = make_glb(tmp_path / 'part-0000.glb', ['one', 'two'])
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'format': 'solaris-animation-packs-v1', 'packs': [{'id': 'part-0000', 'clips': ['two']}]}))
    selected = preflight([path], load_manifest(manifest, [path]))
    result = tmp_path / 'filtered.glb'
    filtered_glb(path, result, selected[path])
    assert [item['name'] for item in read_glb(result)[0]['animations']] == ['two']
    assert result.read_bytes().endswith(b'1234')


def test_missing_and_colliding_names_rejected(tmp_path):
    one = make_glb(tmp_path / 'one.glb', ['same', 'extra'])
    two = make_glb(tmp_path / 'two.glb', ['same'])
    with pytest.raises(ValueError, match='missing clips'):
        preflight([one], {'one': ['absent']})
    with pytest.raises(ValueError, match='collision'):
        preflight([one, two], None)
    with pytest.raises(ValueError, match='collision'):
        preflight([one], None, ['same'])
    assert preflight([one, two], {'one': ['extra'], 'two': ['same']})[one] == {'extra'}


def test_duplicate_and_long_names_rejected(tmp_path):
    with pytest.raises(ValueError, match='duplicate'):
        preflight([make_glb(tmp_path / 'dup.glb', ['x', 'x'])], None)
    with pytest.raises(ValueError, match='limit'):
        preflight([make_glb(tmp_path / 'long.glb', ['я' * 32])], None)


def test_empty_manifest_and_pack_list(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'format': 'solaris-animation-packs-v1', 'packs': []}))
    assert load_manifest(path, []) == {}
    assert preflight([], {}) == {}


def test_manifest_requires_exact_pack_set_and_unique_clips(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'format': 'solaris-animation-packs-v1', 'packs': [{'id': 'one', 'clips': ['x', 'x']}]}))
    with pytest.raises(ValueError, match='duplicate'):
        load_manifest(path, [tmp_path / 'one.glb'])
    path.write_text(json.dumps({'format': 'solaris-animation-packs-v1', 'packs': [{'id': 'one', 'clips': []}]}))
    with pytest.raises(ValueError, match='match'):
        load_manifest(path, [])


def test_static_scene_without_armature_is_saved_compressed(tmp_path, monkeypatch):
    import sys

    from wuwa_story_worker.blender_ueformat_actions import assemble

    calls = []
    wm = SimpleNamespace(open_mainfile=lambda **kw: calls.append(('open', kw)),
                         save_as_mainfile=lambda **kw: calls.append(('save', kw)))
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(ops=SimpleNamespace(wm=wm)))
    source, output = tmp_path / 'source.blend', tmp_path / 'output.blend'
    assemble(source, output, [])
    assert calls == [('open', {'filepath': str(source)}),
                     ('save', {'filepath': str(output), 'compress': True})]
