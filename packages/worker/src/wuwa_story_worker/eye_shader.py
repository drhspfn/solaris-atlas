"""Blender preview of the EM highlight recovered from the SM5 eye shader.

This does not implement eye parallax, the height-light atlas, or game lighting.
"""

import math
from contextlib import contextmanager
from pathlib import Path

_ADD_NODE = 'Solaris Eye EM Highlight'


def highlight_inputs(metadata, converted):
    """Return validated SM5 inputs, or None when this material cannot use them."""
    if metadata.get('Switches', {}).get('UseHeightLightShape') is not True:
        return None
    reference = (metadata.get('Textures', {}).get('EM') or {}).get('ObjectPath')
    if not isinstance(reference, str) or not reference:
        return None
    path = reference.replace('\\', '/').split('.', 1)[0]
    if path.startswith('/Game/'):
        path = 'Client/Content/' + path[6:]
    root = Path(converted).resolve()
    image = (root / (path + '.png')).resolve()
    if not image.is_relative_to(root) or not image.is_file():
        return None

    scalars = metadata.get('Scalars', {})
    colors = metadata.get('Colors', {})
    color = colors.get('HeightLight_Color')
    if not isinstance(color, dict):
        return None
    values = [scalars.get('HeightLight_PositionX'),
              scalars.get('HeightLight_PositionY'),
              scalars.get('HeightLight_WidthX'),
              scalars.get('HeightLight_WidthY'),
              scalars.get('HeightLightScale'),
              *(color.get(channel) for channel in 'RGBA')]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        return None
    if any(value <= 0 for value in values[2:5]):
        return None
    return image, values


def add_eye_highlight(material, shader, metadata, converted, *, uv_name):
    """Add only EM.r * smoothstep((1-EM.a)/scale) * highlight color."""
    inputs = highlight_inputs(metadata, converted)
    if inputs is None or not uv_name:
        return False
    base = shader.inputs['Base Color']
    if not base.is_linked:
        return False
    if material.node_tree.nodes.get(_ADD_NODE):
        return False

    import bpy

    path, (px, py, wx, wy, scale, red, green, blue, alpha) = inputs
    nodes, links = material.node_tree.nodes, material.node_tree.links
    original = base.links[0].from_socket
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = 'Non-Color'
    image.alpha_mode = 'CHANNEL_PACKED'
    image.pack()
    uv = nodes.new('ShaderNodeUVMap')
    uv.uv_map = uv_name
    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(uv.outputs['UV'], split.inputs[0])

    def operation(kind, a, b):
        node = nodes.new('ShaderNodeMath')
        node.operation = kind
        for socket, value in zip(node.inputs, (a, b), strict=False):
            if isinstance(value, (int, float)):
                socket.default_value = value
            else:
                links.new(value, socket)
        return node.outputs[0]

    # UE V and Blender V have opposite origins. Convert the transformed UE V
    # back to Blender coordinates rather than flipping the source image.
    transformed = nodes.new('ShaderNodeCombineXYZ')
    links.new(operation('ADD', operation('DIVIDE', operation('SUBTRACT', split.outputs['X'], px), wx), .5),
              transformed.inputs['X'])
    links.new(operation('ADD', operation('DIVIDE', operation('ADD', split.outputs['Y'], py - 1), wy), .5),
              transformed.inputs['Y'])
    shifted = nodes.new('ShaderNodeTexImage')
    shifted.image = image
    shifted.label = 'EM.r: transformed UV0'
    links.new(transformed.outputs[0], shifted.inputs['Vector'])
    original_em = nodes.new('ShaderNodeTexImage')
    original_em.image = image
    original_em.label = 'EM.a: original UV0'
    links.new(uv.outputs['UV'], original_em.inputs['Vector'])
    separate = nodes.new('ShaderNodeSeparateColor')
    links.new(shifted.outputs['Color'], separate.inputs['Color'])
    z = operation('DIVIDE', operation('SUBTRACT', 1, original_em.outputs['Alpha']), scale)
    z = operation('MINIMUM', operation('MAXIMUM', z, 0), 1)
    fade = operation('MULTIPLY', operation('MULTIPLY', z, z),
                     operation('SUBTRACT', 3, operation('MULTIPLY', 2, z)))
    amount = operation('MULTIPLY', separate.outputs['Red'], fade)
    amount = operation('MULTIPLY', amount, alpha)
    color = nodes.new('ShaderNodeRGB')
    color.outputs[0].default_value = (red, green, blue, 1)
    contribution = nodes.new('ShaderNodeVectorMath')
    contribution.operation = 'SCALE'
    links.new(color.outputs[0], contribution.inputs[0])
    links.new(amount, contribution.inputs[3])
    add = nodes.new('ShaderNodeMixRGB')
    add.name = _ADD_NODE
    add.label = 'SM5 EM highlight only (Blender preview)'
    add.blend_type = 'ADD'
    add.inputs[0].default_value = 1
    links.new(original, add.inputs[1])
    links.new(contribution.outputs[0], add.inputs[2])
    links.new(add.outputs[0], base)
    return True


@contextmanager
def gltf_eye_fallback(meshes):
    """Export the unmodified PBR base color; keep the preview in the .blend."""
    changed = []
    try:
        materials = {material for mesh in meshes for material in mesh.data.materials if material}
        for material in materials:
            if not material.use_nodes:
                continue
            nodes, links = material.node_tree.nodes, material.node_tree.links
            add = nodes.get(_ADD_NODE)
            shader = nodes.get('Principled BSDF')
            if not add or not shader or not add.inputs[1].is_linked:
                continue
            base = shader.inputs['Base Color']
            if not base.is_linked or base.links[0].from_node != add:
                continue
            links.new(add.inputs[1].links[0].from_socket, base)
            changed.append((links, add.outputs[0], base))
        yield
    finally:
        for links, output, base in changed:
            links.new(output, base)
