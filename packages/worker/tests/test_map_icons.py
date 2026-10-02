import pytest
from wuwa_story_worker.map_icons import sprite_box


def test_atlas_uv_crop_and_bounds():
    assert sprite_box({'uv0X': .1, 'uv3X': .3, 'uv0Y': .4,
                      'uv3Y': .2}, 1000, 500) == (100, 100, 300, 200)
    with pytest.raises(ValueError):
        sprite_box({'uv0X': -.1, 'uv3X': .3,
                   'uv0Y': .4, 'uv3Y': .2}, 1000, 500)
