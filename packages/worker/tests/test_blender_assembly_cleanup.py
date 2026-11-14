import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(not os.environ.get('SOLARIS_BLENDER'), reason='SOLARIS_BLENDER is not configured')
def test_cleanup_removes_only_unused_imported_datablocks(tmp_path):
    script = tmp_path / 'check.py'
    script.write_text('''\
import bpy, sys
sys.path.insert(0, sys.argv[-1])
from wuwa_story_worker.blender_scene_assembly import snapshot_temporary_data, cleanup_temporary_data
bpy.ops.wm.read_factory_settings(use_empty=True)
old_orphan = bpy.data.meshes.new('old orphan')
old_image = bpy.data.images.new('old image', 2, 2)
old_mesh = bpy.data.meshes.new('old mesh')
old_object = bpy.data.objects.new('old object', old_mesh)
bpy.context.scene.collection.objects.link(old_object)
before = snapshot_temporary_data(bpy)
used = bpy.data.materials.new('still used')
old_mesh.materials.append(used)
cleanup_temporary_data(bpy, before)
assert bpy.data.materials.get('still used') == used
old_mesh.materials.clear()
cleanup_temporary_data(bpy, before)
assert bpy.data.materials.get('still used') is None
for i in range(3):
    collection = bpy.data.collections.new('temporary')
    bpy.context.scene.collection.children.link(collection)
    mesh = bpy.data.meshes.new('temporary mesh')
    obj = bpy.data.objects.new('temporary object', mesh)
    collection.objects.link(obj)
    material = bpy.data.materials.new('temporary material')
    material.use_nodes = True
    image = bpy.data.images.new('temporary image', 2, 2)
    material.node_tree.nodes.new('ShaderNodeTexImage').image = image
    mesh.materials.append(material)
    armature = bpy.data.armatures.new('temporary armature')
    action = bpy.data.actions.new('retained action ' + str(i))
    action.use_fake_user = True
    bpy.data.objects.remove(obj, do_unlink=True)
    cleanup_temporary_data(bpy, before)
    assert set(bpy.data.meshes) == before['meshes']
    assert set(bpy.data.armatures) == before['armatures']
    assert set(bpy.data.materials) == before['materials']
    assert set(bpy.data.images) == before['images']
    assert set(bpy.data.collections) == before['collections']
    assert bpy.data.objects.get('old object') == old_object
    assert bpy.data.actions.get(action.name) == action
assert bpy.data.meshes.get('old orphan') == old_orphan
assert bpy.data.images.get('old image') == old_image
''', encoding='utf8')
    source = Path(__file__).resolve().parents[1] / 'src'
    subprocess.run([os.environ['SOLARIS_BLENDER'], '-b', '-t', '2', '--python-exit-code', '1',
                    '--python', str(script), '--', str(source)], check=True, timeout=60)
