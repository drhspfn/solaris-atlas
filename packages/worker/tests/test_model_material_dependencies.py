import json

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
