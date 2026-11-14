import math

import pytest

from wuwa_story_worker.eye_height_map import height_map_transform


SCALARS = {'ScaleTransition_Speed': 5, 'EyeLight_TransitionScale': .5,
           'UseEyeScaleTransition': 0, 'LightDifferentScale': 1,
           'LightShakeSpeed': 10, 'LightShakeScale': .01, 'LightShakPositionX': 0,
           'LightShakPositionY': 0, 'LightNormlScale': 0, 'LightPositionX': 0,
           'LightPositionY': 0, 'SecondLight_WidthX': .866215,
           'SecondLight_WidthY': .664286, 'SecondLight_PositionX': .47881,
           'SecondLight_PositionY': .340952, 'SecondLight_Position_02X': -.049999,
           'SecondLight_Position_02Y': 0, 'RotateAngle': .042938}


def test_height_map_transform_uses_authored_width_position_and_vertex_delta():
    width, pos, delta, angle = height_map_transform(SCALARS, 0)
    assert width == pytest.approx((.225 * .866215, .225 * .664286))
    assert pos == pytest.approx((.52881, .340952))
    assert delta == pytest.approx((-.049999, 0))
    assert angle == .042938
    animated = height_map_transform({**SCALARS, 'UseEyeScaleTransition': 1}, 0)
    assert animated[0] == pytest.approx(tuple(x * 1.25 for x in width))


def test_height_map_transform_rejects_unknown_and_singular_data():
    assert height_map_transform({}, 0) is None
    assert height_map_transform(SCALARS, math.nan) is None
    assert height_map_transform({**SCALARS, 'LightDifferentScale': 0}, 0) is None
    assert height_map_transform({**SCALARS, 'LightNormlScale': .225 * .866215}, 0) is None
    assert height_map_transform({**SCALARS, 'SecondLight_WidthX': -1}, 0) is not None
