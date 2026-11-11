from wuwa_story_worker.model_material_inventory import material_input_inventory


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
