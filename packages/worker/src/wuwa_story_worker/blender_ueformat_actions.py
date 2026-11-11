"""Attach GLB action packs to a pre-existing UEFormat Blender scene.

Run: blender -b --python blender_ueformat_actions.py -- source.blend output.blend part.glb [...]
"""
import argparse
import importlib.util
from pathlib import Path
import sys


def load_compatibility_check():
    path = Path(__file__).with_name('blender_scene_assembly.py')
    spec = importlib.util.spec_from_file_location('_solaris_scene_assembly', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.incompatible_skeletons


def assemble(source_path, output_path, part_paths, armature_name=None, tolerance=2e-5):
    import bpy
    inputs = [Path(source_path).resolve(), *(Path(p).resolve() for p in part_paths)]
    if Path(output_path).resolve() in inputs:
        raise ValueError('Output must not overwrite an input')
    bpy.ops.wm.open_mainfile(filepath=str(source_path))
    rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE' and (armature_name is None or o.name == armature_name)]
    if len(rigs) != 1:
        raise ValueError(f'Expected one target armature, found {len(rigs)}; specify --armature')
    target = rigs[0]
    target.animation_data_create()
    check = load_compatibility_check()
    count = 0
    for path in part_paths:
        before_objects = set(bpy.data.objects)
        before_actions = set(bpy.data.actions)
        bpy.ops.import_scene.gltf(filepath=str(path))
        imported = set(bpy.data.objects) - before_objects
        rigs = [o for o in imported if o.type == 'ARMATURE']
        if len(rigs) != 1:
            raise ValueError(f'{path}: expected one armature, found {len(rigs)}')
        reason = check(target, rigs[0], tolerance=tolerance)
        if reason:
            raise ValueError(f'{path}: incompatible skeleton: {reason}')
        actions = sorted(set(bpy.data.actions) - before_actions, key=lambda action: action.name)
        if not actions:
            raise ValueError(f'{path}: no actions')
        for action in actions:
            if len(action.slots) != 1 or action.slots[0].target_id_type != 'OBJECT':
                raise ValueError(f'{path}: unsupported slots in {action.name}')
            slot = action.slots[0]
            slot.name_display = target.name
            action.use_fake_user = True
            if count == 0 and target.animation_data.action is None:
                target.animation_data.action = action
                target.animation_data.action_slot = slot
            count += 1
        for obj in imported:
            bpy.data.objects.remove(obj, do_unlink=True)
        print(f'ASSEMBLY {path}: {len(actions)} actions', flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))
    print(f'ASSEMBLY_RESULT actions={count} output={output_path}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--armature')
    parser.add_argument('source')
    parser.add_argument('output')
    parser.add_argument('parts', nargs='+')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    assemble(args.source, args.output, args.parts, args.armature)


if __name__ == '__main__':
    main()
