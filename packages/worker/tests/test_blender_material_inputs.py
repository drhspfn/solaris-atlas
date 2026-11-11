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
