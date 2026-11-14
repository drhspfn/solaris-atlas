"""Add a reusable editable studio-lighting rig to a copy of a Blender scene.

This is an artistic baseline, not extracted game lighting. Existing lights are
hidden in the output copy, not deleted. Run in Blender with -- for arguments.
"""
import argparse
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if args.output.exists():
        raise FileExistsError(args.output)
    bpy.ops.wm.open_mainfile(filepath=str(args.scene.resolve()))
    scene = bpy.context.scene
    if not scene.camera:
        raise ValueError('A camera is required to orient the studio lights')
    bpy.context.view_layer.update()
    meshes = [o for o in scene.objects if o.type == 'MESH' and not o.hide_render]
    if not meshes:
        raise ValueError('No visible meshes to light')
    points = [o.matrix_world @ Vector(corner) for o in meshes for corner in o.bound_box]
    lo = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    hi = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    center = (lo + hi) / 2
    size = max(hi - lo)
    if size <= 0:
        raise ValueError('Meshes have an empty bounding box')
    for obj in scene.objects:
        if obj.type == 'LIGHT':
            obj.hide_render = True
            obj.hide_viewport = True
    collection = bpy.data.collections.new('Solaris studio - adjustable')
    scene.collection.children.link(collection)
    scene.world = bpy.data.worlds.new('Solaris studio ambient')
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get('Background')
    background.inputs['Color'].default_value = (.35, .38, .42, 1)
    background.inputs['Strength'].default_value = .25
    rotation = scene.camera.matrix_world.to_quaternion()
    right = rotation @ Vector((1, 0, 0))
    up = rotation @ Vector((0, 1, 0))
    toward_camera = rotation @ Vector((0, 0, 1))
    for name, x, y, z, energy, width in (
        ('Key', 1.0, 1.5, 2.0, 160, 2.0),
        ('Fill', -1.5, .5, 1.5, 65, 2.5),
        ('Rim', -1.0, 1.0, -1.5, 130, 1.5),
    ):
        data = bpy.data.lights.new('Solaris ' + name, type='AREA')
        data.energy = energy * size * size
        data.shape = 'DISK'
        data.size = width * size
        obj = bpy.data.objects.new(data.name, data)
        collection.objects.link(obj)
        obj.location = center + size * (right * x + up * y + toward_camera * z)
        obj.rotation_euler = (center - obj.location).to_track_quat('-Z', 'Y').to_euler()
    scene['Solaris lighting'] = 'Artistic studio baseline, not reconstructed game lighting'
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))


if __name__ == '__main__':
    main()
