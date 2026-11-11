"""Attach selected GLB actions to a pre-existing UEFormat Blender scene.

Run: blender -b --python blender_ueformat_actions.py -- [--manifest file.json] source.blend output.blend [part.glb ...]
The manifest uses solaris-animation-packs-v1 and addresses each part by its filename stem.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile


def load_compatibility_check():
    path = Path(__file__).with_name('blender_scene_assembly.py')
    spec = importlib.util.spec_from_file_location('_solaris_scene_assembly', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.incompatible_skeletons


def read_glb(path):
    """Read only the JSON chunk; retain the binary chunks byte-for-byte."""
    with Path(path).open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError(f'{path}: truncated GLB header')
        magic, version, total = struct.unpack('<4sII', header)
        if magic != b'glTF' or version != 2 or total != Path(path).stat().st_size:
            raise ValueError(f'{path}: invalid GLB header')
        chunk = stream.read(8)
        if len(chunk) != 8:
            raise ValueError(f'{path}: missing JSON chunk')
        length, kind = struct.unpack('<I4s', chunk)
        if kind != b'JSON' or length % 4 or length > total - 20:
            raise ValueError(f'{path}: invalid JSON chunk')
        data = stream.read(length)
        document = json.loads(data)
        remainder_offset = 20 + length
    return document, remainder_offset


def load_manifest(path, part_paths):
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('format') != 'solaris-animation-packs-v1' or not isinstance(data.get('packs'), list):
        raise ValueError('Invalid animation pack manifest')
    requested = {}
    for pack in data['packs']:
        if not isinstance(pack, dict) or not isinstance(pack.get('id'), str) or not isinstance(pack.get('clips'), list):
            raise ValueError('Invalid animation pack entry')
        name, clips = pack['id'], pack['clips']
        if name in requested or not name or any(not isinstance(c, str) or not c for c in clips) or len(set(clips)) != len(clips):
            raise ValueError(f'Invalid or duplicate pack/clips: {name}')
        requested[name] = clips
    stems = [Path(p).stem for p in part_paths]
    if len(stems) != len(set(stems)) or set(stems) != set(requested):
        raise ValueError('Manifest pack IDs must match the supplied pack filenames')
    return requested


def preflight(part_paths, manifest, existing_names=()):
    """Resolve raw GLB animation names before Blender can suffix collisions."""
    selected = {}
    seen = set(existing_names)
    for path in part_paths:
        document, _ = read_glb(path)
        animations = document.get('animations', [])
        names = [item.get('name') if isinstance(item, dict) else None for item in animations]
        if not names or any(not isinstance(n, str) or not n for n in names) or len(names) != len(set(names)):
            raise ValueError(f'{path}: missing or duplicate GLB animation names')
        clips = manifest[Path(path).stem] if manifest is not None else names
        missing = set(clips) - set(names)
        if missing:
            raise ValueError(f'{path}: missing clips: {sorted(missing)}')
        for name in clips:
            if len(name.encode('utf-8')) > 63:
                raise ValueError(f'{path}: animation name exceeds Blender limit: {name}')
            if name in seen:
                raise ValueError(f'{path}: animation name collision: {name}')
            seen.add(name)
        selected[Path(path)] = set(clips)
    return selected


def filtered_glb(source, destination, selected):
    document, offset = read_glb(source)
    document['animations'] = [a for a in document['animations'] if a['name'] in selected]
    data = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    data += b' ' * (-len(data) % 4)
    remainder_size = Path(source).stat().st_size - offset
    with Path(source).open('rb') as src, Path(destination).open('wb') as dst:
        dst.write(struct.pack('<4sII', b'glTF', 2, 20 + len(data) + remainder_size))
        dst.write(struct.pack('<I4s', len(data), b'JSON'))
        dst.write(data)
        src.seek(offset)
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)


def assemble(source_path, output_path, part_paths, armature_name=None, tolerance=2e-5, manifest_path=None):
    import bpy
    inputs = [Path(source_path).resolve(), *(Path(p).resolve() for p in part_paths)]
    if manifest_path is not None:
        inputs.append(Path(manifest_path).resolve())
    if Path(output_path).resolve() in inputs:
        raise ValueError('Output must not overwrite an input')
    manifest = load_manifest(manifest_path, part_paths)
    # Check each GLB and cross-pack collisions before changing Blender state.
    preflight(part_paths, manifest)
    bpy.ops.wm.open_mainfile(filepath=str(source_path))
    if not part_paths:
        bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)
        print(f'ASSEMBLY_RESULT actions=0 output={output_path}', flush=True)
        return
    rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE' and (armature_name is None or o.name == armature_name)]
    if len(rigs) != 1:
        raise ValueError(f'Expected one target armature, found {len(rigs)}; specify --armature')
    target = rigs[0]
    selected = preflight(part_paths, manifest, bpy.data.actions.keys())
    target.animation_data_create()
    check = load_compatibility_check()
    count = 0
    with tempfile.TemporaryDirectory(prefix='solaris-blender-actions-') as temp:
        for index, path in enumerate(part_paths):
            clips = selected[Path(path)]
            if not clips:
                continue
            filtered = Path(temp) / f'{index}.glb'
            filtered_glb(path, filtered, clips)
            before_objects = set(bpy.data.objects)
            before_actions = set(bpy.data.actions)
            bpy.ops.import_scene.gltf(filepath=str(filtered))
            imported = set(bpy.data.objects) - before_objects
            rigs = [o for o in imported if o.type == 'ARMATURE']
            if len(rigs) != 1:
                raise ValueError(f'{path}: expected one armature, found {len(rigs)}')
            reason = check(target, rigs[0], tolerance=tolerance)
            if reason:
                raise ValueError(f'{path}: incompatible skeleton: {reason}')
            actions = sorted(set(bpy.data.actions) - before_actions, key=lambda action: action.name)
            if {a.name for a in actions} != clips:
                raise ValueError(f'{path}: Blender changed or lost action names')
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
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), compress=True)
    print(f'ASSEMBLY_RESULT actions={count} output={output_path}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--armature')
    parser.add_argument('--manifest')
    parser.add_argument('source')
    parser.add_argument('output')
    parser.add_argument('parts', nargs='*')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    assemble(args.source, args.output, args.parts, args.armature, manifest_path=args.manifest)


if __name__ == '__main__':
    main()
