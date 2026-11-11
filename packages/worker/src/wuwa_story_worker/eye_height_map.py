"""Opt-in SM5 HeightLightMap coordinates; no guessed runtime inputs or lighting."""

import math


_NAMES = ('ScaleTransition_Speed', 'EyeLight_TransitionScale', 'UseEyeScaleTransition',
          'LightDifferentScale', 'LightShakeSpeed', 'LightShakeScale', 'LightShakPositionX',
          'LightShakPositionY', 'LightNormlScale', 'LightPositionX', 'LightPositionY',
          'SecondLight_WidthX', 'SecondLight_WidthY', 'SecondLight_PositionX',
          'SecondLight_PositionY', 'SecondLight_Position_02X', 'SecondLight_Position_02Y',
          'RotateAngle')


def height_map_transform(scalars, time):
    """Return constants for UE UV mapping, or None for absent/singular inputs."""
    if not isinstance(scalars, dict) or type(time) not in (int, float) or not math.isfinite(time):
        return None
    if any(type(scalars.get(k)) not in (int, float) or not math.isfinite(scalars[k]) for k in _NAMES):
        return None
    p = scalars
    phase = (math.sin(time * p['ScaleTransition_Speed'] * 6.283185) + 1) * .5
    shake = math.sin(time * p['LightShakeSpeed'] * 6.283185) * p['LightShakeScale']
    scale = 1 + phase * p['EyeLight_TransitionScale'] if p['UseEyeScaleTransition'] >= .5 else 1
    width = tuple((.225 * p['SecondLight_Width' + axis] * scale - p['LightNormlScale'])
                  * p['LightDifferentScale'] for axis in 'XY')
    if any(w == 0 or not math.isfinite(w) for w in width):
        return None
    pos = tuple(p['SecondLight_Position' + axis] + p['LightShakPosition' + axis] * shake
                + p['LightPosition' + axis] + (.05 if axis == 'X' else 0) for axis in 'XY')
    delta = tuple(p['SecondLight_Position_02' + axis] for axis in 'XY')
    return width, pos, delta, p['RotateAngle']


def add_height_map_sample(material, image, uv_socket, green_socket, scalars, time, *,
                          uv_flip_v, texture_flip_v):
    """Return an unconnected texture node. Inputs must be explicitly verified by caller.

    uv_socket is mesh UV0; green_socket must carry the original normalized game
    COLOR.g, with any Blender color-space conversion handled by the caller.
    Image color space, sampling and mip bias are not inferred or modified.
    The node supplies map RGB, not the final eye color or a complete material.
    """
    transform = height_map_transform(scalars, time)
    if (transform is None or material is None or not material.use_nodes or image is None
            or uv_socket is None or green_socket is None or type(uv_flip_v) is not bool
            or type(texture_flip_v) is not bool):
        return None
    width, pos, delta, angle = transform
    nodes, links = material.node_tree.nodes, material.node_tree.links

    def op(kind, a, b):
        node = nodes.new('ShaderNodeMath')
        node.operation = kind
        for socket, value in zip(node.inputs, (a, b)):
            if isinstance(value, (int, float)):
                socket.default_value = value
            else:
                links.new(value, socket)
        return node.outputs[0]

    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(uv_socket, split.inputs[0])
    u, v = split.outputs['X'], split.outputs['Y']
    if uv_flip_v:
        v = op('SUBTRACT', 1, v)
    x = op('DIVIDE', op('SUBTRACT', u, op('ADD', pos[0], op('MULTIPLY', green_socket, delta[0]))), width[0])
    y = op('DIVIDE', op('SUBTRACT', v, op('ADD', pos[1], op('MULTIPLY', green_socket, delta[1]))), width[1])
    c, s = math.cos(angle), math.sin(angle)
    rx = op('ADD', op('SUBTRACT', op('MULTIPLY', x, c), op('MULTIPLY', y, s)), .5)
    ry = op('ADD', op('ADD', op('MULTIPLY', x, s), op('MULTIPLY', y, c)), .5)
    if texture_flip_v:
        ry = op('SUBTRACT', 1, ry)
    coords = nodes.new('ShaderNodeCombineXYZ')
    links.new(rx, coords.inputs['X'])
    links.new(ry, coords.inputs['Y'])
    texture = nodes.new('ShaderNodeTexImage')
    texture.image = image
    texture.label = 'SM5 HeightLightMap UV0 (diagnostic sample)'
    links.new(coords.outputs[0], texture.inputs['Vector'])
    return texture
