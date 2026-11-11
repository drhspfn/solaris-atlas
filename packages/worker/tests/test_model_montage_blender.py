import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not os.environ.get('SOLARIS_BLENDER'), reason='SOLARIS_BLENDER is not configured')
def test_blender_nla_trim_rate_loops_and_reverse(tmp_path):
    root = Path(__file__).resolve().parents[1] / 'src'
    script = tmp_path / 'check.py'
    script.write_text('''\
import bpy, sys
sys.path.insert(0, sys.argv[-1])
from wuwa_story_worker.model_montage_blender import create_montage_track
for rate in (2, -2):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 10
    bpy.context.scene.render.fps_base = 1
    bpy.ops.object.armature_add()
    rig = bpy.context.object
    rig.animation_data_create()
    action = bpy.data.actions.new('segment')
    rig.animation_data.action = action
    slot = action.slots.new(id_type='OBJECT', name=rig.name)
    rig.animation_data.action_slot = slot
    curve = action.fcurves.new('location', index=0)
    curve.keyframe_points.insert(10, 1)
    curve.keyframe_points.insert(30, 3)
    for key in curve.keyframe_points: key.interpolation = 'LINEAR'
    rig.animation_data.action = None
    segment = {'reference':'clip','start':0,'end':2,'anim_start':1,'anim_end':3,'rate':rate,'loops':2}
    create_montage_track(rig, {'name':'montage','segments':[segment],'sections':[]}, {'clip':action}, fps=10)
    for frame in (2, 7, 12, 17):
        bpy.context.scene.frame_set(frame)
        expected = (1 + (frame % 10) / 5) if rate > 0 else (3 - (frame % 10) / 5)
        assert abs(rig.location.x - expected) < 1e-4, (rate, frame, rig.location.x, expected)
''', encoding='utf8')
    subprocess.run([os.environ['SOLARIS_BLENDER'], '-b', '-t', '2', '--python-exit-code', '1',
                    '--python', str(script), '--', str(root)], check=True, timeout=60)
