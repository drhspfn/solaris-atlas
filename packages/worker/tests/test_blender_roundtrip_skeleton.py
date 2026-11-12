import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.environ.get('SOLARIS_BLENDER'), reason='SOLARIS_BLENDER is not configured')
def test_roundtrip_compares_same_skeleton_and_rejects_real_difference(tmp_path):
    script = tmp_path / 'check.py'
    script.write_text('''\
import bpy, sys
from pathlib import Path
sys.path.insert(0, sys.argv[-2])
from wuwa_story_worker.blender_ueformat_actions import glb_roundtrip_rig, load_compatibility_check, bind_roundtrip_driver
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.object.armature_add()
target = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bone = target.data.edit_bones[0]
bone.name = 'Bone_Prop011'
bone.roll = 3.14155
bpy.ops.object.mode_set(mode='OBJECT')
original = target.data.bones['Bone_Prop011'].matrix_local.copy()
canonical, imported = glb_roundtrip_rig(target, Path(sys.argv[-1]) / 'canonical.glb', bpy=bpy)
incoming, imported2 = glb_roundtrip_rig(target, Path(sys.argv[-1]) / 'incoming.glb', bpy=bpy)
check = load_compatibility_check()
assert check(canonical, incoming, tolerance=2e-5) is None
assert target.data.bones['Bone_Prop011'].matrix_local == original
assert bpy.context.view_layer.objects.active == target and target.select_get()
bpy.ops.object.select_all(action='DESELECT')
incoming.select_set(True)
bpy.context.view_layer.objects.active = incoming
bpy.ops.object.mode_set(mode='EDIT')
incoming.data.edit_bones['Bone_Prop011'].head.x += .01
bpy.ops.object.mode_set(mode='OBJECT')
assert check(canonical, incoming, tolerance=2e-5) is not None
bone = canonical.pose.bones['Bone_Prop011']
bone.location.x = 0
bone.keyframe_insert(data_path='location', frame=0)
bone.location.x = 0.4
bone.keyframe_insert(data_path='location', frame=10)
bind_roundtrip_driver(target, canonical)
for frame in (0, 3, 7, 10):
    bpy.context.scene.frame_set(frame)
    a = target.matrix_world @ target.pose.bones['Bone_Prop011'].matrix
    b = canonical.matrix_world @ canonical.pose.bones['Bone_Prop011'].matrix
    assert max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)) < 1e-6
try: bind_roundtrip_driver(target, canonical)
except ValueError: pass
else: raise AssertionError('existing constraints were silently replaced')
''', encoding='utf8')
    source = Path(__file__).resolve().parents[1] / 'src'
    subprocess.run([os.environ['SOLARIS_BLENDER'], '-b', '-t', '2', '--python-exit-code', '1',
                    '--python', str(script), '--', str(source), str(tmp_path)], check=True, timeout=60)
