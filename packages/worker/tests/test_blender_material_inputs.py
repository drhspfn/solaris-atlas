import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def mapper(monkeypatch):
    monkeypatch.setitem(sys.modules, 'bpy', types.ModuleType('bpy'))
    path = Path(__file__).resolve().parents[3] / 'scripts/blender_material_inputs.py'
    spec = importlib.util.spec_from_file_location('blender_material_inputs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolves_exact_game_and_converted_paths(mapper, tmp_path):
    texture = tmp_path / 'Client/Content/Aki/Texture/Test.png'
    texture.parent.mkdir(parents=True)
    texture.touch()
    for reference in ('/Game/Aki/Texture/Test', 'Client/Content/Aki/Texture/Test.1'):
        assert mapper.resolve_texture({'ObjectPath': reference}, [tmp_path]) == texture


def test_rejects_untrusted_or_missing_paths(mapper, tmp_path):
    for reference in ('/Game/../../outside', '/Other/Test', 'Client/Content/missing', None):
        assert mapper.resolve_texture({'ObjectPath': reference}, [tmp_path]) is None


def test_packed_diffuse_for_character_materials_but_not_effects(mapper):
    assert mapper.uses_packed_character_diffuse({'BlendMode': 0, 'Switches': {'UseHairVCGradient': True}})
    assert mapper.uses_packed_character_diffuse({'BlendMode': 1, 'Switches': {'UsePBR': True}})
    assert not mapper.uses_packed_character_diffuse({'BlendMode': 2, 'Switches': {'UseHairVCGradient': True}})
    assert not mapper.uses_packed_character_diffuse({'BlendMode': 0, 'Switches': {}})


def test_water_gradient_requires_authored_features_and_gradient_texture(mapper):
    switches = {'UseWaterClothColor': True, 'UseMatcap': True}
    assert mapper.uses_water_gradient(switches, (512, 4))
    assert not mapper.uses_water_gradient(switches, (512, 512))
    assert not mapper.uses_water_gradient(switches, (0, 0))
    assert not mapper.uses_water_gradient({'UseMatcap': True}, (512, 4))
