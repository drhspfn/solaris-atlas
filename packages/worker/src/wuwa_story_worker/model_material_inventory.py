"""Preserve authored material inputs without guessing shader semantics.

The converter's resolved material maps contain parameters, not the original
shader graph. Consumers must not infer texture channels or blending formulas
from parameter names alone.
"""

import json
from pathlib import Path, PurePosixPath


def material_input_inventory(metadata, converted_root):
    """Return authored inputs and the availability of their converted images.

    ``converted_root`` is the directory containing ``Client/Content``. Texture
    references outside /Game, malformed paths, and absent images remain in the
    inventory with no local path; none are substituted with a nearby asset.
    """
    root = (Path(converted_root) / 'Client' / 'Content').resolve()
    textures = {}
    for name, entry in (metadata.get('Textures') or {}).items():
        if not isinstance(entry, dict) or not isinstance(entry.get('ObjectPath'), str):
            continue
        source = entry['ObjectPath']
        local = None
        if source.startswith('/Game/'):
            package = source[len('/Game/'):].split('.', 1)[0]
            parts = PurePosixPath(package).parts
            if parts and all(part not in ('.', '..') and '\\' not in part for part in parts):
                candidate = root.joinpath(*parts).with_suffix('.png').resolve()
                if candidate.is_relative_to(root) and candidate.is_file():
                    local = candidate.relative_to(root).as_posix()
        textures[name] = {'source': source, 'image': local}
    return {
        'textures': textures,
        'scalars': dict(metadata.get('Scalars') or {}),
        'colors': dict(metadata.get('Colors') or {}),
        'switches': dict(metadata.get('Switches') or {}),
    }


def pack_material_inventory(material, inventory, converted_root):
    """Pack available images and attach the inventory to a Blender material.

    This records parameters as metadata only; it does not create shader links.
    Missing images remain recorded with no Blender image name. Returns the
    metadata saved on the material. Callers save the .blend in the usual way.
    """
    import bpy

    root = (Path(converted_root) / 'Client' / 'Content').resolve()
    result = {key: dict(inventory.get(key) or {}) for key in ('scalars', 'colors', 'switches')}
    result['textures'] = {}
    for name, entry in (inventory.get('textures') or {}).items():
        source, relative = entry.get('source'), entry.get('image')
        record = {'source': source, 'image': relative, 'blender_image': None}
        if isinstance(relative, str):
            parts = PurePosixPath(relative).parts
            if (parts and all(part not in ('.', '..') and '\\' not in part for part in parts)
                    and not PurePosixPath(relative).is_absolute()):
                path = root.joinpath(*parts).resolve()
                if path.is_relative_to(root) and path.is_file():
                    # A prior packed image may no longer have an accessible filepath.
                    # Store its canonical source so repeated calls reuse the datablock.
                    image = next((item for item in bpy.data.images
                                  if item.get('solaris_inventory_path') == str(path)), None)
                    if image is None:
                        image = bpy.data.images.load(str(path), check_existing=True)
                    image.pack()
                    image.use_fake_user = True
                    image['solaris_inventory_path'] = str(path)
                    record['blender_image'] = image.name
        result['textures'][name] = record
    material['solaris_material_inventory'] = json.dumps(result, ensure_ascii=False, sort_keys=True)
    return result
