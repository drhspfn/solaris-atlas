"""Assemble a Blender scene from an existing rig GLB and animation-part GLBs.

Run with Blender: blender -b --python blender_scene_assembly.py -- rig.glb output.blend part.glb [...]
"""
import argparse
import json
import math
import sys
from pathlib import Path


def incompatible_skeletons(base, incoming, tolerance=1e-5):
    """Compare armature objects in world space as well as every bone rest pose."""
    if set(base.data.bones.keys()) != set(incoming.data.bones.keys()):
        return "bone names differ"
    def different(a, b):
        return any(not math.isfinite(float(a[i][j])) or not math.isfinite(float(b[i][j])) or
                   abs(a[i][j] - b[i][j]) > tolerance for i in range(4) for j in range(4))
    if different(base.matrix_world, incoming.matrix_world):
        return "armature world transforms differ"
    for bone in base.data.bones:
        other = incoming.data.bones[bone.name]
        if (bone.parent.name if bone.parent else None) != (other.parent.name if other.parent else None):
            return f"bone parent differs: {bone.name}"
        if different(bone.matrix_local, other.matrix_local):
            return f"bone rest pose differs: {bone.name}"
    return None


def assemble(rig_path, part_paths, output_path):
    import bpy
    inputs = [Path(rig_path).resolve(), *(Path(path).resolve() for path in part_paths)]
    if Path(output_path).resolve() in inputs:
        raise ValueError("Output must not overwrite an input")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(rig_path))
    rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
    if len(rigs) != 1:
        raise ValueError(f"Expected one base armature, found {len(rigs)}")
    base = rigs[0]
    base.animation_data_create()
    seen_names = set()
    count = 0
    for path in part_paths:
        before_objects = set(bpy.data.objects)
        before_actions = set(bpy.data.actions)
        bpy.ops.import_scene.gltf(filepath=str(path))
        imported = set(bpy.data.objects) - before_objects
        rigs = [o for o in imported if o.type == 'ARMATURE']
        if len(rigs) != 1:
            raise ValueError(f"{path}: expected one armature, found {len(rigs)}")
        source = rigs[0]
        reason = incompatible_skeletons(base, source)
        if reason:
            raise ValueError(f"{path}: incompatible skeleton: {reason}")
        actions = sorted(set(bpy.data.actions) - before_actions, key=lambda action: action.name)
        for action in actions:
            if action.name in seen_names:
                raise ValueError(f"Duplicate action: {action.name}")
            slots = [slot for slot in action.slots if slot.target_id_type == 'OBJECT']
            if len(slots) != 1 or len(action.slots) != 1:
                raise ValueError(f"{path}: unsupported slots in {action.name}")
            # Slot names are used by Blender when selecting an action in the UI.
            # Preserve the slot itself, including its channelbag and f-curves.
            slots[0].name_display = base.name
            action.use_fake_user = True
            if count == 0:
                base.animation_data.action = action
                base.animation_data.action_slot = slots[0]
            seen_names.add(action.name)
            count += 1
        for obj in imported:
            bpy.data.objects.remove(obj, do_unlink=True)
        print(f"ASSEMBLY {path}: {len(actions)} actions", flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))
    print('ASSEMBLY_RESULT ' + json.dumps({'actions': count, 'output': str(output_path)}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('rig')
    parser.add_argument('output')
    parser.add_argument('parts', nargs='+')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    assemble(args.rig, args.parts, args.output)


if __name__ == '__main__':
    main()
