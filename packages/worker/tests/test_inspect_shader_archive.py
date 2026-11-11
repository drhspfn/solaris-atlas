import importlib.util
import struct
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'inspect_shader_archive.py'
spec = importlib.util.spec_from_file_location('inspect_shader_archive', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_archive(path, index=0):
    def u32(value):
        return struct.pack('<I', value)
    path.write_bytes(b''.join([
        u32(2), u32(1), bytes.fromhex('01' * 20), u32(1), bytes.fromhex('02' * 20),
        u32(1), struct.pack('<IIII', 0, 1, 0, 0), u32(1), struct.pack('<QIIB', 0, 4, 4, 5),
        u32(0), u32(1), u32(index), b'test',
    ]))


def test_join_uses_resource_hash_and_index(tmp_path):
    path = tmp_path / 'archive'
    make_archive(path)
    archive = module.load_library(path)
    exports = [{'Name': 'fan', 'LoadedMaterialResources': [{'LoadedShaderMap': {
        'ShaderMapId': {'FeatureLevel': 'SM5', 'QualityLevel': 'High'}, 'ResourceHash': '01' * 20,
        'Content': {'OrderedMeshShaderMaps': [{'VertexFactoryTypeName': 'skin',
            'Shaders': [{'Type': 'pixel', 'ResourceIndex': 0}]}]},
    }}]}]
    result = module.lookup_material(archive, exports, 'SM5')
    assert result[0]['shaders'][0]['hash'] == '02' * 20
    assert result[0]['quality_level'] == 'High'
    assert result[0]['roles'][0]['archive_shader_index'] == 0
    assert module.lookup_material(archive, exports, 'SM6') == []
    exports[0]['LoadedMaterialResources'][0]['LoadedShaderMap']['Content']['OrderedMeshShaderMaps'][0]['Shaders'][0]['ResourceIndex'] = 1
    with pytest.raises(ValueError, match='outside shader map'):
        module.lookup_material(archive, exports, 'SM5')


def test_rejects_invalid_indices_and_truncated_data(tmp_path):
    path = tmp_path / 'archive'
    make_archive(path, index=1)
    with pytest.raises(ValueError, match='unknown shader'):
        module.load_library(path)
    make_archive(path)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError):
        module.load_library(path)
