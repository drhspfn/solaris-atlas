"""Create an inspectable Blender material from cooked instance parameters.

Run with Blender --background --python this_file -- --help. This is a partial
mapper: only an explicitly named diffuse texture is connected. Other inputs are
kept as labelled nodes; the cooked shader program is not reconstructed.
"""
import argparse
import json
import sys
from pathlib import Path

import bpy


def resolve_texture(reference, roots):
    if not isinstance(reference, dict):
        return None
    path = reference.get('ObjectPath')
    if not isinstance(path, str):
        return None
    path = path.replace('\\', '/').split('.', 1)[0]
    if path.startswith('/Game/'):
        path = 'Client/Content/' + path[6:]
    if not path.startswith('Client/Content/'):
        return None
    for root in roots:
        candidate = (root / (path + '.png')).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    return None


def build_material(material, metadata, roots):
    textures = metadata.get('Textures') or {}
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    links = material.node_tree.links
    shader = nodes.new('ShaderNodeBsdfPrincipled')
    shader.location = (300, 0)
    shader.inputs['Roughness'].default_value = 0.85
    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (600, 0)
    links.new(shader.outputs['BSDF'], output.inputs['Surface'])
    missing, available = [], {}
    for index, (name, reference) in enumerate(textures.items()):
        # The converter includes extra aliases named after the texture itself.
        object_name = reference.get('ObjectName', '') if isinstance(reference, dict) else ''
        if "'" in object_name and name == object_name.split("'")[1]:
            continue
        path = resolve_texture(reference, roots)
        if path is None:
            missing.append(name)
            continue
        node = nodes.new('ShaderNodeTexImage')
        node.label = name
        node.name = 'UE texture: ' + name
        node.location = (-500 - 310 * (index // 10), -300 * (index % 10))
        node.image = bpy.data.images.load(str(path), check_existing=True)
        node.image.pack()
        available[name] = node
    base = next((available[name] for name in ('MainTex', 'BaseColor', 'Base') if name in available), None)
    if base:
        links.new(base.outputs['Color'], shader.inputs['Base Color'])
    for key in ('Scalars', 'Colors', 'Switches'):
        # Preserve parameter values without inventing their cooked semantics.
        material['UE ' + key] = json.dumps(metadata.get(key) or {}, ensure_ascii=False)
    material['UE mapper status'] = 'partial: diffuse only; remaining shader logic not recovered'
    return {'material': material.name, 'base': base.label if base else None,
            'available': list(available), 'missing': missing, 'status': 'partial'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', required=True, type=Path)
    parser.add_argument('--metadata', required=True, action='append', type=Path)
    parser.add_argument('--textures', required=True, action='append', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if args.output.exists() or args.report.exists():
        raise FileExistsError('Output and report must not already exist')
    roots = [path.resolve() for path in args.textures]
    bpy.ops.wm.open_mainfile(filepath=str(args.scene.resolve()))
    results = []
    for path in args.metadata:
        material = bpy.data.materials.get(path.stem)
        if material is None:
            raise ValueError(f'Material not in scene: {path.stem}')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or not isinstance(data.get('Textures'), dict):
            raise TypeError(f'Unsupported material metadata: {path}')
        results.append(build_material(material, data, roots))
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))
    args.report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
