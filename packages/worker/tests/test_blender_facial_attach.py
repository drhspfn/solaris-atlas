import os
import json
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(not os.environ.get('SOLARIS_BLENDER'), reason='SOLARIS_BLENDER is not configured')
def test_facial_slots_multiple_keys_body_and_standalone(tmp_path):
    script = tmp_path / 'check.py'
    script.write_text('''\
import bpy, sys
sys.path.insert(0, sys.argv[-1])
from wuwa_story_worker.blender_facial_attach import attach_facial_pack, select_facial_action
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.render.fps = 24
bpy.ops.object.armature_add()
rig = bpy.context.object
body = bpy.data.actions.new('Body')
body.slots.new(id_type='OBJECT', name=rig.name)
keys = []
for i in range(2):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.shape_key_add(name='Basis')
    obj.shape_key_add(name='Smile')
    keys.append(obj.data.shape_keys)
pack = {'format':'solaris-facial-v1', 'clips':[
    {'name':'Linked', 'body_clip':'Body', 'seconds':1, 'tracks':[{'target':'smile','keys':[[0,0],[1,1]]}]},
    {'name':'Standalone', 'body_clip':None, 'seconds':1, 'tracks':[{'target':'Smile','keys':[[0,1],[1,0]]}]},
]}
missing_track = {'format':pack['format'], 'clips':[{'name':'bad', 'body_clip':None, 'seconds':1,
    'tracks':[{'target':'absent','keys':[[0,0]]}]}]}
missing_body = {'format':pack['format'], 'clips':[{'name':'bad', 'body_clip':'absent', 'seconds':1,
    'tracks':[{'target':'Smile','keys':[[0,0]]}]}]}
for invalid in (missing_track, missing_body):
    try: attach_facial_pack(invalid, bpy=bpy, strict=True)
    except ValueError: pass
    else: raise AssertionError('strict preflight accepted missing input')
    assert not any(a.get('solaris_facial_clip') for a in bpy.data.actions)
result = attach_facial_pack(pack, bpy=bpy, strict=True)
assert result['clips'] == 2 and result['keyframes'] == 8
linked = next(a for a in bpy.data.actions if a.get('solaris_facial_clip') == 'Linked')
assert len(linked.slots) == 2
select_facial_action(linked, bpy=bpy, body_rig=rig)
bpy.context.scene.frame_set(12)
assert rig.animation_data.action == body
assert all(abs(k.key_blocks['Smile'].value - .5) < 1e-5 for k in keys)
standalone = next(a for a in bpy.data.actions if a.get('solaris_facial_clip') == 'Standalone')
select_facial_action(standalone, bpy=bpy, body_rig=rig)
bpy.context.scene.frame_set(6)
assert rig.animation_data.action == body
assert all(abs(k.key_blocks['Smile'].value - .75) < 1e-5 for k in keys)
''', encoding='utf8')
    source = Path(__file__).resolve().parents[1] / 'src'
    subprocess.run([os.environ['SOLARIS_BLENDER'], '-b', '-t', '2', '--python-exit-code', '1',
                    '--python', str(script), '--', str(source)], check=True, timeout=60)


@pytest.mark.skipif(not os.environ.get('SOLARIS_BLENDER'), reason='SOLARIS_BLENDER is not configured')
def test_cli_strict_does_not_save_on_missing_body(tmp_path):
    make_scene = tmp_path / 'scene.py'
    scene = tmp_path / 'source.blend'
    make_scene.write_text('''\
import bpy,sys
bpy.ops.mesh.primitive_cube_add()
bpy.context.object.shape_key_add(name='Basis')
bpy.context.object.shape_key_add(name='Smile')
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
''', encoding='utf8')
    blender = os.environ['SOLARIS_BLENDER']
    subprocess.run([blender, '-b', '-t', '2', '--python-exit-code', '1', '--python', str(make_scene),
                    '--', str(scene)], check=True, timeout=60, capture_output=True)
    facial = tmp_path / 'facial.json'
    facial.write_text(json.dumps({'format': 'solaris-facial-v1', 'clips': [
        {'name': 'missing', 'body_clip': 'absent', 'seconds': 1,
         'tracks': [{'target': 'Smile', 'keys': [[0, 0]]}]}
    ]}), encoding='utf8')
    out = tmp_path / 'result.blend'
    module = Path(__file__).resolve().parents[1] / 'src/wuwa_story_worker/blender_facial_attach.py'
    result = subprocess.run([blender, '-b', '-t', '2', str(scene), '--python-exit-code', '1',
                             '--python', str(module), '--', str(facial), str(out), '--strict'],
                            timeout=60, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'missing or ambiguous body action' in result.stderr + result.stdout
    assert not out.exists()
