"""Attach independently saved Blender components using an evidence-backed transform.

Run with Blender: -- root.blend child.blend manifest.json output.blend
The manifest specifies Blender-space matrices, an existing parent bone and a source
reference. It does not infer animation synchronization or game visibility cues.
"""
import argparse
import json
import math
import sys
from pathlib import Path


def validate_manifest(data):
    for key in ('root_rig', 'bone', 'child_root', 'evidence'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f'Missing {key}')
    for key in ('bone_to_anchor', 'anchor_to_child'):
        matrix = data.get(key)
        if not isinstance(matrix, list) or len(matrix) != 4 or any(not isinstance(row, list) or len(row) != 4 for row in matrix):
            raise ValueError(f'{key} must be a 4x4 matrix')
        if any(type(v) not in (int, float) or not math.isfinite(v) for row in matrix for v in row):
            raise ValueError(f'{key} must contain finite numbers')
        if any(abs(a-b) > 1e-8 for a,b in zip(matrix[3], (0,0,0,1), strict=True)):
            raise ValueError(f'{key} must be affine')
    return data


def combine(root_path, child_path, manifest_path, output_path):
    import bpy
    from mathutils import Matrix
    data = validate_manifest(json.loads(Path(manifest_path).read_text(encoding='utf-8-sig')))
    if Path(output_path).resolve() in {Path(p).resolve() for p in (root_path, child_path, manifest_path)}:
        raise ValueError('Output must not overwrite an input')
    bpy.ops.wm.open_mainfile(filepath=str(root_path))
    root = bpy.data.objects.get(data['root_rig'])
    if root is None or root.type != 'ARMATURE' or data['bone'] not in root.pose.bones:
        raise ValueError('Root armature or attachment bone not found')
    with bpy.data.libraries.load(str(child_path), link=False) as (src, dst):
        if data['child_root'] not in src.objects:
            raise ValueError('Child root not found')
        names = tuple(src.objects)
        dst.objects = list(names)
        dst.actions = list(src.actions)
    objects = dict(zip(names, dst.objects, strict=True))
    if any(obj is None for obj in objects.values()):
        raise ValueError('Child object could not be appended')
    child = objects[data['child_root']]
    if child.type != 'ARMATURE' or child.parent is not None:
        raise ValueError('Child root must be an unparented armature')
    for obj in objects.values():
        bpy.context.scene.collection.objects.link(obj)
    for action in dst.actions:
        if action is not None:
            action.use_fake_user = True
    anchor = bpy.data.objects.new('Solaris attachment: ' + data['child_root'], None)
    bpy.context.scene.collection.objects.link(anchor)
    anchor.parent = root
    anchor.parent_type = 'BONE'
    anchor.parent_bone = data['bone']
    anchor.matrix_parent_inverse = Matrix.Identity(4)
    bpy.context.view_layer.update()
    anchor.matrix_world = root.matrix_world @ root.pose.bones[data['bone']].matrix @ Matrix(data['bone_to_anchor'])
    anchor['solaris_attachment_evidence'] = data['evidence']
    anchor['solaris_animation_sync'] = 'not established'
    child.parent = anchor
    child.matrix_parent_inverse = Matrix.Identity(4)
    child.matrix_basis = Matrix(data['anchor_to_child'])
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)
    print(f'LINKED_RESULT bone={data["bone"]} child={child.name} objects={len(objects)} actions={len(dst.actions)}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('child')
    parser.add_argument('manifest')
    parser.add_argument('output')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    combine(args.root, args.child, args.manifest, args.output)


if __name__ == '__main__':
    main()
