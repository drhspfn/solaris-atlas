import json

import pytest

from wuwa_story_worker.model_material_dependencies import missing_material_resources


def put(root, package, value):
    path = root / "Client/Content" / (package + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_follows_exact_shared_parent_and_texture_without_scanning_other_materials(tmp_path):
    cooked, converted = tmp_path / "cooked", tmp_path / "converted"
    put(cooked, "Body", [{"Type": "SkeletalMesh", "SkeletalMaterials": [
        {"Material": {"ObjectPath": "/Game/MI_Body.MI_Body"}}]}])
    put(cooked, "MI_Body", [{"Properties": {"Parent": {
        "ObjectName": "MaterialInstanceConstant'MI_Base'", "ObjectPath": "/Game/MI_Base.MI_Base"}}}])
    put(converted, "MI_Body", {"Textures": {"MainTex": {"ObjectPath": "/Game/Shared/Color"}}})
    put(converted, "Unrelated", {"Textures": {"MainTex": {"ObjectPath": "/Game/NeverFetch"}}})
    assert missing_material_resources(cooked, converted, ["Body"]) == ["MI_Base", "Shared/Color"]
    put(cooked, "MI_Base", [{"Properties": {"Parent": {
        "ObjectName": "MaterialInstanceConstant'MI_Body'", "ObjectPath": "/Game/MI_Body"}}}])
    put(converted, "MI_Base", {})
    texture = converted / "Client/Content/Shared/Color.png"
    texture.parent.mkdir(parents=True)
    texture.touch()
    assert missing_material_resources(cooked, converted, ["Body"]) == []

def test_textureless_raw_parent_material_is_supported_without_inventing_a_texture(tmp_path):
    from wuwa_story_worker.model_bundle import base_color_texture, material_metadata
    raw = [{'Type': 'MaterialInstanceConstant', 'Name': 'MI_CharNPCDyeFace', 'Properties': {
        'Parent': {'ObjectName': "Material'Master'", 'ObjectPath': '/Game/Master.0'},
        'ScalarParameterValues': [{'ParameterInfo': {'Name': 'RefractionDepthBias'}, 'ParameterValue': 0.0}],
        'StaticParameters': {'StaticSwitchParameters': [
            {'ParameterInfo': {'Name': 'UseIDColor'}, 'Value': True, 'bOverride': True}]}}}]
    metadata = material_metadata(raw)
    assert metadata['Scalars'] == {'RefractionDepthBias': 0.0}
    assert metadata['Switches'] == {'UseIDColor': True}
    assert base_color_texture(raw, tmp_path) == (None, None)
    cooked, converted = tmp_path / 'cooked', tmp_path / 'converted'
    put(cooked, 'Body', [{'Type': 'SkeletalMesh', 'SkeletalMaterials': [
        {'Material': {'ObjectPath': '/Game/Child.0'}}]}])
    put(cooked, 'Child', [{'Properties': {'Parent': {
        'ObjectName': "MaterialInstanceConstant'Parent'", 'ObjectPath': '/Game/Parent.0'}}}])
    put(converted, 'Child', {'Textures': {}})
    put(cooked, 'Parent', raw)
    put(converted, 'Parent', raw)
    assert missing_material_resources(cooked, converted, ['Body']) == []


def test_raw_material_color_parameter_uses_the_exact_reference(tmp_path):
    from wuwa_story_worker.model_bundle import base_color_texture
    raw = [{'Type': 'MaterialInstanceConstant', 'Properties': {
        'TextureParameterValues': [{'ParameterInfo': {'Name': 'MainTex'},
                                    'ParameterValue': {'ObjectPath': '/Game/ExactColor.0'}}]}}]
    assert base_color_texture(raw, tmp_path) == ('/Game/ExactColor.0', None)


def test_authored_stencil_mask_is_fetched_and_preserved_with_its_slot(tmp_path):
    import base64
    from wuwa_story_worker.model_bundle import stencil_mask_payload

    cooked, converted = tmp_path / 'cooked', tmp_path / 'converted'
    model = converted / 'Client/Content/Body.uemodel'
    put(cooked, 'Body', [{'Type': 'SkeletalMesh', 'SkeletalMaterials': [
        {'MaterialSlotName': 'HET_Face', 'Material': {'ObjectPath': '/Game/FaceMask.0'}}]}])
    put(cooked, 'FaceMask', [{'Type': 'MaterialInstanceConstant', 'Properties': {
        'Parent': {'ObjectPath': '/Game/Aki/Render/Shaders/Character/M_WriteStencil'}}}])
    put(converted, 'FaceMask', {'Textures': {'Mask': {'ObjectPath': '/Game/Textures/Face.0'}}})
    assert missing_material_resources(cooked, converted, ['Body']) == ['Textures/Face']
    image = converted / 'Client/Content/Textures/Face.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'mask image')
    assert missing_material_resources(cooked, converted, ['Body']) == []
    payload = stencil_mask_payload([model], converted, cooked)
    assert payload['format'] == 'solaris-hair-stencil-v1'
    assert payload['masks'][0]['slot'] == 'HET_Face'
    assert payload['masks'][0]['texture'] == '/Game/Textures/Face.0'
    assert base64.b64decode(payload['masks'][0]['data_base64']) == b'mask image'


def test_stencil_reader_must_be_an_authored_hair_pass_with_an_authored_parent(tmp_path):
    from wuwa_story_worker.model_bundle import stencil_mask_payload

    cooked, converted = tmp_path / 'cooked', tmp_path / 'converted'
    model = converted / 'Client/Content/Body.uemodel'
    put(cooked, 'Body', [{'Type': 'SkeletalMesh', 'SkeletalMaterials': [
        {'MaterialSlotName': 'MI_Bangs', 'Material': {'ObjectPath': '/Game/Bangs.0'}},
        {'MaterialSlotName': 'HETA_Bangs', 'Material': {'ObjectPath': '/Game/HairPass.0'}},
        {'MaterialSlotName': 'HET_Face', 'Material': {'ObjectPath': '/Game/Face_HET.0'}},
        {'MaterialSlotName': 'MI_Face', 'Material': {'ObjectPath': '/Game/Face.0'}}]}])
    put(cooked, 'HairPass', [{'Type': 'MaterialInstanceConstant', 'Properties': {
        'Parent': {'ObjectPath': '/Game/Bangs.0'}}}])
    put(converted, 'HairPass', {'Switches': {'IsHairHET': True}})
    put(cooked, 'Face_HET', [{'Type': 'MaterialInstanceConstant', 'Properties': {
        'Parent': {'ObjectPath': '/Game/Aki/Render/Shaders/Character/M_WriteStencil'}}}])
    put(converted, 'Face_HET', {'Textures': {'Mask': {'ObjectPath': '/Game/FaceMask.0'}}})
    image = converted / 'Client/Content/FaceMask.png'
    image.write_bytes(b'mask')
    result = stencil_mask_payload([model], converted, cooked)
    assert result['readers'][0]['material'] == '/Game/Bangs'
    assert result['masks'][0]['target_material'] == '/Game/Face'


@pytest.mark.parametrize('family', ['WaterMatCap', 'ParallaxSubUV', 'Refraction'])
def test_effect_base_dependency_requires_known_parent(tmp_path, family):
    from wuwa_story_worker.model_bundle import inherited_base_color_texture

    cooked, converted = tmp_path / 'cooked', tmp_path / 'converted'
    name = 'MI_Refraction_CM' if family == 'Refraction' else f'MI_AdvancedEffect_{family}_CM'
    parent = f'Aki/Render/MaterialInstance/Effect/CharacterMesh/{name}'
    put(cooked, 'Body', [{'Type': 'StaticMesh', 'StaticMaterials': [
        {'MaterialInterface': {'ObjectPath': '/Game/Fan.0'}}]}])
    put(cooked, 'Fan', [{'Type': 'MaterialInstanceConstant', 'Properties': {'Parent': {
        'ObjectName': "MaterialInstanceConstant'Parent'", 'ObjectPath': f'/Game/{parent}.0'}}}])
    put(converted, 'Fan', {'Textures': {'Base': {'ObjectPath': '/Game/Effect/Exact.0'}}})
    put(cooked, parent, [])
    put(converted, parent, {})
    assert missing_material_resources(cooked, converted, ['Body']) == ['Effect/Exact']
    put(cooked, 'Fan', [{'Type': 'MaterialInstanceConstant', 'Properties': {'Parent': {
        'ObjectName': "MaterialInstanceConstant'Parent'", 'ObjectPath': f'Client/Content/{parent}.0'}}}])
    assert missing_material_resources(cooked, converted, ['Body']) == ['Effect/Exact']
    texture = converted / 'Client/Content/Effect/Exact.png'
    texture.parent.mkdir(parents=True)
    texture.touch()
    assert missing_material_resources(cooked, converted, ['Body']) == []
    path = converted / 'Client/Content/Fan.json'
    assert inherited_base_color_texture(json.loads(path.read_text()), path, converted, cooked) == (
        '/Game/Effect/Exact.0', texture)
    put(cooked, 'Fan', [{'Type': 'MaterialInstanceConstant', 'Properties': {'Parent': {
        'ObjectName': "Material'Other'", 'ObjectPath': '/Game/Other.0'}}}])
    texture.unlink()
    assert missing_material_resources(cooked, converted, ['Body']) == []
