import base64

from wuwa_story_worker.eye_shader import highlight_inputs
from wuwa_story_worker.model_bundle import eye_material_payload


def test_eye_payload_includes_authored_hdr_colors(tmp_path):
    image = tmp_path / 'Client/Content/Eye/EM.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'png bytes')
    color = {'R': 2.853229, 'G': 1.296875, 'B': 3.0, 'A': .05}
    metadata = {'Textures': {'EM': {'ObjectPath': '/Game/Eye/EM.EM'}},
                'Scalars': {'HeightLight_PositionX': .5, 'HeightLight_PositionY': .619048,
                            'HeightLight_WidthX': 2, 'HeightLight_WidthY': 1.480201,
                            'HeightLightScale': .288095},
                'Colors': {'HeightLight_Color': color},
                'Switches': {'UseHeightLightShape': True}}
    payload = eye_material_payload(metadata, tmp_path)
    assert payload['colors']['HeightLight_Color'] == color
    assert base64.b64decode(payload['textures']['EM']['data_base64']) == b'png bytes'
    assert highlight_inputs(metadata, tmp_path)[1][5:] == list(color.values())
    metadata['Scalars']['HeightLightScale'] = 0
    assert highlight_inputs(metadata, tmp_path) is None


def test_eye_shader_ignores_other_materials_and_missing_textures(tmp_path):
    assert eye_material_payload({'Colors': {'HeightLight_Color': {}}}, tmp_path) is None
    assert highlight_inputs({'Switches': {'UseHeightLightShape': True}}, tmp_path) is None
    metadata = {'Switches': {'UseHeightLightShape': True},
                'Textures': {'EM': {'ObjectPath': '../outside'}}}
    assert highlight_inputs(metadata, tmp_path) is None
