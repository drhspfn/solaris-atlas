"""Preserve authored material inputs without guessing shader semantics.

The converter's resolved material maps contain parameters, not the original
shader graph. Consumers must not infer texture channels or blending formulas
from parameter names alone.
"""

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
