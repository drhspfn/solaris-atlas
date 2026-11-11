import json
import sys
from types import SimpleNamespace

from wuwa_story_worker.model_material_inventory import material_input_inventory, pack_material_inventory


def test_preserves_authored_inputs_and_distinguishes_missing_images(tmp_path):
    image = tmp_path / 'Client/Content/Aki/Textures/Hair.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'png')
    metadata = {
        'Textures': {
            'MainTex': {'ObjectPath': '/Game/Aki/Textures/Hair.Hair'},
            'Noise': {'ObjectPath': '/Game/Aki/Textures/Absent'},
            'Invalid': {'ObjectPath': '/Game/../../outside'},
        },
        'Scalars': {'Speed': 0.25}, 'Colors': {'Tint': {'R': 1}},
        'Switches': {'Enabled': False},
    }
    result = material_input_inventory(metadata, tmp_path)
    assert result['textures']['MainTex']['image'] == 'Aki/Textures/Hair.png'
    assert result['textures']['Noise']['image'] is None
    assert result['textures']['Invalid']['image'] is None
    assert result['scalars'] == {'Speed': 0.25}
    assert result['colors'] == {'Tint': {'R': 1}}
    assert result['switches'] == {'Enabled': False}


def test_does_not_resolve_external_or_windows_paths(tmp_path):
    result = material_input_inventory({'Textures': {
        'external': {'ObjectPath': '/Engine/Asset'},
        'windows': {'ObjectPath': '/Game/..\\secret'},
        'invalid': None,
    }}, tmp_path)
    assert result['textures']['external']['image'] is None
    assert result['textures']['windows']['image'] is None
    assert 'invalid' not in result['textures']


def test_pack_reuses_images_records_parameters_and_rejects_unsafe_paths(tmp_path, monkeypatch):
    class Image(dict):
        def __init__(self, name):
            super().__init__()
            self.name = name
            self.use_fake_user = False
            self.packed = False

        def pack(self):
            self.packed = True

    class Images(list):
        def load(self, path, check_existing):
            assert check_existing
            image = Image('Hair')
            self.append(image)
            return image

    images = Images()
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(data=SimpleNamespace(images=images)))
    image = tmp_path / 'Client/Content/Hair.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'png')
    inventory = {'textures': {
        'MainTex': {'source': '/Game/Hair', 'image': 'Hair.png'},
        'Alias': {'source': '/Game/Hair', 'image': 'Hair.png'},
        'Outside': {'source': '/Game/X', 'image': '../../outside.png'},
        'Missing': {'source': '/Game/Missing', 'image': None},
    }, 'scalars': {'Speed': 1}}
    material = {}
    result = pack_material_inventory(material, inventory, tmp_path)
    pack_material_inventory(material, inventory, tmp_path)
    assert len(images) == 1
    assert images[0].packed and images[0].use_fake_user
    assert result['textures']['MainTex']['blender_image'] == result['textures']['Alias']['blender_image'] == 'Hair'
    assert result['textures']['Outside']['blender_image'] is None
    assert json.loads(material['solaris_material_inventory']) == result
