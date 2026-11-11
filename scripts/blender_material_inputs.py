"""Create an inspectable Blender material from cooked instance parameters.

Run with Blender --background --python this_file -- --help. This is a partial
mapper: only an explicitly named diffuse texture is connected. Other inputs are
kept as labelled nodes; the cooked shader program is not reconstructed.
"""
import argparse
import json
import math
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


def uses_packed_character_diffuse(metadata):
    switches = metadata.get('Switches') or {}
    return (metadata.get('BlendMode') in (0, 1)
            and (switches.get('UseHairVCGradient') is True or switches.get('UsePBR') is True))


def uses_water_gradient(switches, size):
    return (switches.get('UseWaterClothColor') is True and switches.get('UseMatcap') is True
            and size[0] > 0 and size[1] > 0 and size[0] >= 4 * size[1])


def wave_uv_parameters(metadata):
    value = (metadata.get('Colors') or {}).get('Base_UV') or {}
    if not isinstance(value, dict):
        return None
    values = [value.get(key) for key in ('R', 'G', 'B', 'A')]
    if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 100 for v in values):
        return None
    return tuple(values)


def animate_wave_uv(nodes, links, image_node, metadata):
    params = wave_uv_parameters(metadata)
    if params is None:
        return False
    scale_x, scale_y, speed_x, speed_y = params
    coords = nodes.new('ShaderNodeTexCoord')
    mapping = nodes.new('ShaderNodeMapping')
    mapping.label = 'Approximation: UV scale and XY drift'
    mapping.inputs['Scale'].default_value = (scale_x, scale_y, 1)
    links.new(coords.outputs['UV'], mapping.inputs['Vector'])
    links.new(mapping.outputs['Vector'], image_node.inputs['Vector'])
    for axis, speed in enumerate((speed_x, speed_y)):
        driver = mapping.inputs['Location'].driver_add('default_value', axis).driver
        for name, path in (('fps', 'render.fps'), ('fps_base', 'render.fps_base')):
            variable = driver.variables.new()
            variable.name = name
            variable.targets[0].id_type = 'SCENE'
            variable.targets[0].id = bpy.context.scene
            variable.targets[0].data_path = path
        driver.expression = f'frame * fps_base / fps * {speed!r}'
    return True


def add_approximate_shading(material, shader, available, metadata):
    """Build editable approximations; none of these nodes are decoded UE graphs."""
    nodes, links = material.node_tree.nodes, material.node_tree.links
    original_nodes = set(nodes)
    switches = metadata.get('Switches') or {}
    scalars = metadata.get('Scalars') or {}
    applied = []
    normal = available.get('Normal_Roughness_Metallic')
    if normal and (switches.get('UseNormalMap') is True or switches.get('UseNormalTex') is True):
        # Hypothesis: R/G encode tangent-space X/Y. Reconstruct positive Z.
        # Copy the image: colorspace is per-image and must not change color nodes.
        normal.image = normal.image.copy()
        normal.image.colorspace_settings.name = 'Non-Color'
        normal.image.alpha_mode = 'CHANNEL_PACKED'
        normal.image.pack()
        separate = nodes.new('ShaderNodeSeparateColor')
        links.new(normal.outputs['Color'], separate.inputs['Color'])
        squares = []
        for channel in ('Red', 'Green'):
            remap = nodes.new('ShaderNodeMath')
            remap.operation = 'MULTIPLY_ADD'
            remap.inputs[1].default_value = 2
            remap.inputs[2].default_value = -1
            links.new(separate.outputs[channel], remap.inputs[0])
            square = nodes.new('ShaderNodeMath')
            square.operation = 'MULTIPLY'
            links.new(remap.outputs[0], square.inputs[0])
            links.new(remap.outputs[0], square.inputs[1])
            squares.append(square.outputs[0])
        add = nodes.new('ShaderNodeMath')
        add.operation = 'ADD'
        links.new(squares[0], add.inputs[0])
        links.new(squares[1], add.inputs[1])
        remaining = nodes.new('ShaderNodeMath')
        remaining.operation = 'SUBTRACT'
        remaining.use_clamp = True
        remaining.inputs[0].default_value = 1
        links.new(add.outputs[0], remaining.inputs[1])
        sqrt = nodes.new('ShaderNodeMath')
        sqrt.operation = 'SQRT'
        links.new(remaining.outputs[0], sqrt.inputs[0])
        blue = nodes.new('ShaderNodeMath')
        blue.operation = 'MULTIPLY_ADD'
        blue.inputs[1].default_value = .5
        blue.inputs[2].default_value = .5
        links.new(sqrt.outputs[0], blue.inputs[0])
        combine = nodes.new('ShaderNodeCombineColor')
        links.new(separate.outputs['Red'], combine.inputs['Red'])
        links.new(separate.outputs['Green'], combine.inputs['Green'])
        links.new(blue.outputs[0], combine.inputs['Blue'])
        normal_map = nodes.new('ShaderNodeNormalMap')
        normal_map.label = 'Approximation: reconstruct positive Z from RG'
        strength = scalars.get('NormalStrength', 1)
        if type(strength) in (float, int) and 0 <= strength <= 10:
            normal_map.inputs['Strength'].default_value = strength
        links.new(combine.outputs['Color'], normal_map.inputs['Color'])
        links.new(normal_map.outputs['Normal'], shader.inputs['Normal'])
        shader.inputs['Roughness'].default_value = .58
        applied.append('normal_rg_positive_z_assumed')
    if switches.get('UseHairVCGradient') is True:
        shader.inputs['Roughness'].default_value = .48
        applied.append('hair_roughness_artistic')
    if 'MainTex' in available and (normal or switches.get('UseHairVCGradient') is True):
        # A small adjustable unlit contribution mitigates double-lighting
        # painted diffuse textures. It is not a recovered game lighting model.
        links.new(available['MainTex'].outputs['Color'], shader.inputs['Emission Color'])
        shader.inputs['Emission Strength'].default_value = .3
        applied.append('painted_diffuse_fill_artistic')
    if metadata.get('BlendMode') == 2 and 'Base' in available:
        second = available.get('Second')
        if second and uses_water_gradient(switches, second.image.size):
            # Artistic water/matcap approximation: the authored gradient is a
            # color lookup, driven here by the observed wave texture intensity.
            # This is not the game's recovered material graph.
            intensity = nodes.new('ShaderNodeRGBToBW')
            intensity.label = 'Approximation: wave intensity'
            if animate_wave_uv(nodes, links, available['Base'], metadata):
                applied.append('water_uv_drift_assumed')
            links.new(available['Base'].outputs['Color'], intensity.inputs['Color'])
            gradient_position = nodes.new('ShaderNodeMath')
            gradient_position.operation = 'MULTIPLY_ADD'
            gradient_position.use_clamp = True
            gradient_position.label = 'Approximation: water gradient range'
            gradient_position.inputs[1].default_value = -.25
            gradient_position.inputs[2].default_value = .68
            links.new(intensity.outputs['Val'], gradient_position.inputs[0])
            gradient_uv = nodes.new('ShaderNodeCombineXYZ')
            gradient_uv.label = 'Approximation: sample water gradient'
            gradient_uv.inputs['Y'].default_value = .5
            links.new(gradient_position.outputs[0], gradient_uv.inputs['X'])
            links.new(gradient_uv.outputs['Vector'], second.inputs['Vector'])
            links.new(second.outputs['Color'], shader.inputs['Base Color'])
            links.new(second.outputs['Color'], shader.inputs['Emission Color'])
            glow = nodes.new('ShaderNodeMath')
            glow.operation = 'MULTIPLY_ADD'
            glow.label = 'Approximation: illuminated wave crests'
            glow.inputs[1].default_value = 1.4
            glow.inputs[2].default_value = .1
            links.new(intensity.outputs['Val'], glow.inputs[0])
            links.new(glow.outputs[0], shader.inputs['Emission Strength'])
            shader.inputs['Roughness'].default_value = .22
            bump = nodes.new('ShaderNodeBump')
            bump.label = 'Approximation: wave relief'
            bump.inputs['Strength'].default_value = .3
            bump.inputs['Distance'].default_value = .01
            links.new(intensity.outputs['Val'], bump.inputs['Height'])
            links.new(bump.outputs['Normal'], shader.inputs['Normal'])
            if switches.get('UseWPO') is True:
                # Cycles-only material displacement: a small visual hypothesis,
                # not a recovered UE World Position Offset formula.
                displacement = nodes.new('ShaderNodeDisplacement')
                displacement.label = 'Approximation: Cycles water displacement'
                displacement.inputs['Scale'].default_value = .006
                links.new(intensity.outputs['Val'], displacement.inputs['Height'])
                output = next(n for n in nodes if n.type == 'OUTPUT_MATERIAL')
                links.new(displacement.outputs['Displacement'], output.inputs['Displacement'])
                material.displacement_method = 'BOTH'
                applied.append('water_cycles_displacement_artistic')
            applied.append('water_bump_artistic')
            applied.append('water_gradient_lookup_artistic')
        elif switches.get('UseSecondTex') is True and second:
            layer = nodes.new('ShaderNodeMixRGB')
            layer.blend_type = 'ADD'
            layer.label = 'Approximation: second texture layer'
            amount = scalars.get('Base_bAddSecond', .1)
            if type(amount) not in (int, float) or not 0 <= amount <= 1:
                amount = .1
            layer.inputs[0].default_value = amount
            links.new(available['Base'].outputs['Color'], layer.inputs[1])
            links.new(available['Second'].outputs['Color'], layer.inputs[2])
            links.new(layer.outputs['Color'], shader.inputs['Base Color'])
            applied.append('effect_second_layer_approximation')
        # Unreal effect graph combines several masks/refraction; simple alpha is
        # an artistic fallback, not an interpretation of the authored equations.
        alpha = nodes.new('ShaderNodeMath')
        alpha.operation = 'MULTIPLY'
        alpha.label = 'Approximation: adjustable effect opacity'
        opacity = scalars.get('Base_Alpha_Multiply')
        alpha.inputs[1].default_value = (
            opacity if switches.get('UseSecondMask') is True and type(opacity) in (int, float)
            and 0 <= opacity <= 1 else .35
        )
        links.new(available['Base'].outputs['Alpha'], alpha.inputs[0])
        links.new(alpha.outputs[0], shader.inputs['Alpha'])
        material.surface_render_method = 'DITHERED'
        if switches.get('UseSecondMask') is True:
            # An editable emissive fill keeps the illustrated overlay legible
            # through the water layer; the game's lighting is not recovered.
            links.new(available['Base'].outputs['Color'], shader.inputs['Emission Color'])
            shader.inputs['Emission Strength'].default_value = 2
            applied.append('effect_overlay_fill_artistic')
        if 'water_gradient_lookup_artistic' not in applied:
            shader.inputs['Roughness'].default_value = .38
        applied.append('translucent_alpha_artistic')
    for index, node in enumerate(n for n in nodes if n not in original_nodes):
        node.location = (-170 - 220 * (index // 6), -240 * (index % 6))
    return applied


def build_material(material, metadata, roots, approximate=False):
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
        # Preserve RGB independent of packed alpha for tested character inputs.
        # The alpha channel remains available for masks. Copy the image because
        # other nodes can share the same source with a different interpretation.
        effect_atlas = (base.label == 'Base' and metadata.get('BlendMode') == 2
                        and (metadata.get('Switches') or {}).get('UseSecondMask') is True)
        if (base.label == 'MainTex' and uses_packed_character_diffuse(metadata)) or effect_atlas:
            base.image = base.image.copy()
            base.image.alpha_mode = 'CHANNEL_PACKED'
            base.image.pack()
        links.new(base.outputs['Color'], shader.inputs['Base Color'])
    for key in ('Scalars', 'Colors', 'Switches'):
        # Preserve parameter values without inventing their cooked semantics.
        material['UE ' + key] = json.dumps(metadata.get(key) or {}, ensure_ascii=False)
    approximations = add_approximate_shading(material, shader, available, metadata) if approximate else []
    material['UE mapper status'] = 'partial: original shader logic not recovered'
    material['UE approximations'] = json.dumps(approximations)
    return {'material': material.name, 'base': base.label if base else None,
            'available': list(available), 'missing': missing, 'status': 'partial',
            'approximations': approximations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', required=True, type=Path)
    parser.add_argument('--metadata', required=True, action='append', type=Path)
    parser.add_argument('--textures', required=True, action='append', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--approximate', action='store_true',
                        help='Add experimental normal, hair and effect shading')
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
        results.append(build_material(material, data, roots, args.approximate))
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))
    args.report.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
