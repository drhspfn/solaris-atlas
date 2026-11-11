import os
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
result = attach_facial_pack(pack, bpy=bpy)
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
