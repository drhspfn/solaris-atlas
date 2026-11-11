"""Build an experimental fan preview in Blender; not a game shader translator.

Run with Blender --background --python this_file -- STUDY_DIRECTORY UEFORMAT_DIR.
The input scene and extracted assets remain untouched; output goes to a new file.
"""
import json
import sys
from pathlib import Path

import bpy
from mathutils import Matrix

root = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
sys.path.insert(0, sys.argv[sys.argv.index('--') + 2])
from io_scene_ueformat.importer.import_context import UEFormatImport
from io_scene_ueformat.options import UEAnimOptions

source = root / 'suisui-studio-material-study.blend'
output = root / 'suisui-fan-material-study.blend'
if output.exists():
    raise FileExistsError(output)
bpy.ops.wm.open_mainfile(filepath=str(source))
body = bpy.data.objects['R2T1SuisuiMd10011_LOD0_Skeleton']
fan = bpy.data.objects['EXPERIMENT - small fan (unattached)']
fan_mesh = next(obj for obj in fan.children if obj.type == 'MESH')

def image_for(parameter, metadata_file, texture_root):
    data = json.loads((root / 'fan-input' / metadata_file).read_text(encoding='utf-8-sig'))
    asset = data['Textures'][parameter]['ObjectPath'].rsplit('/', 1)[-1]
    matches = list((root / texture_root).rglob(asset + '.png'))
    if len(matches) != 1:
        raise ValueError(f'Expected one texture for {asset}, found {len(matches)}')
    return bpy.data.images.load(str(matches[0]), check_existing=True)

def map_base(material_name, parameter, metadata_file, texture_root, opacity):
    material = bpy.data.materials[material_name]
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    tex = nodes.new('ShaderNodeTexImage')
    tex.label = f'Game texture: {parameter}'
    tex.image = image_for(parameter, metadata_file, texture_root)
    shader = nodes.new('ShaderNodeBsdfPrincipled')
    shader.inputs['Roughness'].default_value = 0.45
    out = nodes.new('ShaderNodeOutputMaterial')
    links = material.node_tree.links
    links.new(tex.outputs['Color'], shader.inputs['Base Color'])
    alpha = nodes.new('ShaderNodeMath')
    alpha.operation = 'MULTIPLY'
    alpha.inputs[1].default_value = opacity
    links.new(tex.outputs['Alpha'], alpha.inputs[0])
    links.new(alpha.outputs[0], shader.inputs['Alpha'])
    links.new(shader.outputs['BSDF'], out.inputs['Surface'])
    material.surface_render_method = 'DITHERED'
    material['solaris_note'] = ('Experimental base texture and alpha preview; '
                                'not the cooked Unreal shader or its full blending logic.')

map_base('MI_ESuisuixiaoShuishanMd10011_Shanmian', 'Base',
         'water-material.json', 'fan-texture-converted', 0.35)
map_base('MI_ESuisuixiaoShuishanMd10011_Shanmian_Mountains', 'Base',
         'mountains-material.json', 'fan-mountain-converted', 1.0)

# The blueprint binds OtherCase1 to WeaponProp01. Keep the fan rig so its own
# animations can be imported later. Visibility and animated slot switching are
# not represented in this still preview.
anchor = bpy.data.objects.new('OtherCase1 - WeaponProp01', None)
bpy.context.collection.objects.link(anchor)
anchor.parent = body
anchor.parent_type = 'BONE'
anchor.parent_bone = 'WeaponProp01'
anchor.matrix_parent_inverse = Matrix.Identity(4)
bpy.context.view_layer.update()
anchor.matrix_world = body.matrix_world @ body.data.bones['WeaponProp01'].matrix_local
fan.parent = anchor
fan.matrix_parent_inverse = Matrix.Identity(4)
fan.matrix_basis = Matrix.Identity(4)
fan.name = 'OtherCase1 - small fan rig'
fan.hide_viewport = False
fan.hide_render = False
fan_mesh.hide_viewport = False
fan_mesh.hide_render = False
action = UEFormatImport(UEAnimOptions(override_skeleton=fan)).import_file(
    root / 'fan-input' / 'fan-left-stand2.ueanim'
)
if action is None or not action.fcurves:
    raise ValueError('Fan animation did not produce any curves')
action.name = 'OtherCase1 - Stand2 (component only)'
anchor['source'] = 'BP_R2T1SuisuiMd10011:OtherCase1 / WeaponProp01'
anchor['limitation'] = 'Fan animation only; body synchronization, visibility and slot switching are not reconstructed.'
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(output))
bpy.context.scene.render.filepath = str(root / '04-fan-material-preview.png')
bpy.ops.render.render(write_still=True)
