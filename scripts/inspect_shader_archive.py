"""Inspect UE shader archive v2 and join explicitly exported material shader maps."""
import argparse
import json
import struct
from pathlib import Path


def load_library(path):
    path = Path(path)
    file_size = path.stat().st_size
    with path.open('rb') as stream:
        def take(n):
            if n < 0 or n > file_size - stream.tell():
                raise ValueError('Shader archive extends beyond file')
            data = stream.read(n)
            if len(data) != n:
                raise ValueError('Truncated shader archive')
            return data

        def count(stride):
            value = struct.unpack('<I', take(4))[0]
            if value > (file_size - stream.tell()) // stride:
                raise ValueError('Shader archive count exceeds file bounds')
            return value

        version = struct.unpack('<I', take(4))[0]
        if version != 2:
            raise ValueError(f'Unsupported shader archive version {version}')
        map_hashes = [take(20).hex().upper() for _ in range(count(20))]
        shader_hashes = [take(20).hex().upper() for _ in range(count(20))]
        maps = [struct.unpack('<IIII', take(16)) for _ in range(count(16))]
        shaders = [struct.unpack('<QIIB', take(17)) for _ in range(count(17))]
        if len(map_hashes) != len(maps) or len(shader_hashes) != len(shaders):
            raise ValueError('Hash and entry counts differ')
        take(count(16) * 16)
        indices = [struct.unpack('<I', take(4))[0] for _ in range(count(4))]
        base = stream.tell()
        for offset, n, _, _ in maps:
            if offset + n > len(indices):
                raise ValueError('Shader map indices extend beyond index table')
        if any(i >= len(shaders) for i in indices):
            raise ValueError('Shader map references an unknown shader')
        if any(offset + n > file_size - base for offset, n, _, _ in shaders):
            raise ValueError('Shader data extends beyond archive')
    return {'path': str(path), 'version': version, 'map_hashes': map_hashes,
            'shader_hashes': shader_hashes, 'maps': maps, 'shaders': shaders,
            'indices': indices, 'data_offset': base}


def lookup(archive, map_hash):
    key = map_hash.upper()
    matches = [i for i, h in enumerate(archive['map_hashes']) if h == key]
    result = []
    for index in matches:
        offset, count, preload_offset, preload_count = archive['maps'][index]
        items = []
        for shader_index in archive['indices'][offset:offset + count]:
            byte_offset, packed, unpacked, frequency = archive['shaders'][shader_index]
            items.append({'index': shader_index, 'hash': archive['shader_hashes'][shader_index],
                          'offset': archive['data_offset'] + byte_offset, 'packed': packed,
                          'unpacked': unpacked, 'frequency': frequency})
        result.append({'map_index': index, 'hash': key, 'preload_offset': preload_offset,
                       'preload_count': preload_count, 'shaders': items})
    return result


def lookup_material(archive, exports, feature_level):
    """Join only explicit ResourceHash values; never infer identity from a name."""
    results = []
    for export in exports:
        for resource in export.get('LoadedMaterialResources', []):
            shader_map = resource.get('LoadedShaderMap') or {}
            map_id = shader_map.get('ShaderMapId') or {}
            level = map_id.get('FeatureLevel')
            resource_hash = shader_map.get('ResourceHash')
            if level != feature_level or not isinstance(resource_hash, str):
                continue
            for match in lookup(archive, resource_hash):
                slots = match['shaders']
                roles = []
                content = shader_map.get('Content') or {}
                for group in content.get('OrderedMeshShaderMaps', []):
                    for shader in group.get('Shaders', []):
                        index = shader.get('ResourceIndex')
                        if not isinstance(index, int) or not 0 <= index < len(slots):
                            raise ValueError(f'Material shader ResourceIndex {index!r} is outside shader map')
                        roles.append({'vertex_factory': group.get('VertexFactoryTypeName'),
                                      'shader_type': shader.get('Type'), 'resource_index': index,
                                      'archive_shader_index': slots[index]['index']})
                results.append({'material': export.get('Name'), 'feature_level': level,
                                'quality_level': map_id.get('QualityLevel'),
                                **match, 'roles': roles})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library', type=Path)
    parser.add_argument('--map-hash')
    parser.add_argument('--material-json', type=Path,
                        help='CUE4Parse material export with ReadShaderMaps enabled')
    parser.add_argument('--feature-level', choices=['SM5', 'SM6'], default='SM5')
    args = parser.parse_args()
    archive = load_library(args.library)
    result = {'path': archive['path'], 'version': archive['version'],
              'shader_maps': len(archive['maps']), 'shaders': len(archive['shaders'])}
    if args.map_hash:
        result['matches'] = lookup(archive, args.map_hash)
    if args.material_json:
        exports = json.loads(args.material_json.read_text(encoding='utf-8-sig'))
        if not isinstance(exports, list):
            raise ValueError('Material export must be a JSON array')
        result['materials'] = lookup_material(archive, exports, args.feature_level)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
