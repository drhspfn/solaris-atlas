import copy

import pytest

from wuwa_story_worker.lgui_composition import apply_settled_tracks, visible_layers
from wuwa_story_worker.lgui_properties import property_prefix


def layout():
    def actor(identity, parent, index, kind, props=None, components=None):
        return {'id': identity, 'parent': parent, 'index': index, 'kind': f"Class'{kind}'",
                'active': True, 'props': props or {}, 'components': components or []}
    def rig(identity, index, source):
        return actor(identity, 2, index, 'UISpineRenderable',
                     {'CurrSkeletonData': {'ObjectPath': source}},
                     [{'SkeletonData': {'ObjectPath': source}, 'Atlas': {'ObjectPath': '/Game/Atlas'}}])
    return {'root': 1, 'size': [2560, 1440], 'assets': [], 'actors': {
        1: actor(1, 0, -1, 'UIItem'),
        2: actor(2, 1, 0, 'UIItem', {'RelativeScale3D': [.8, .8, .8]}),
        4: rig(4, 2, '/Game/Foreground'),
        3: rig(3, 1, '/Game/Background'),
    }}


def test_native_hierarchy_orders_layers_and_keeps_authored_canvas_scale():
    source = layout()
    result = visible_layers(source)
    assert [layer['source'] for layer in result] == ['/Game/Background', '/Game/Foreground']
    assert [layer['scale'] for layer in result] == [.8, .8]
    assert source == layout()


def test_settled_ui_binding_hides_exact_actor_without_changing_other_rigs():
    source = layout()
    actors = copy.deepcopy(source['actors'])
    track = {'actor': 4, 'field': 'Alpha', 'points': [(0, 1), (1, 0)]}
    with pytest.raises(ValueError, match='during a UI transition'):
        apply_settled_tracks(actors, [track], .5)
    apply_settled_tracks(actors, [track], 1)
    assert [layer['actor'] for layer in visible_layers(source, actors)] == [3]
    assert len(visible_layers(source)) == 2


def test_ui_transform_uses_horizontal_y_and_vertical_z_not_depth_x():
    source = layout()
    source['actors'][4]['props']['RelativeLocation'] = [99, 30, 40]
    assert visible_layers(source)[1]['origin'] == [24, 32]


@pytest.mark.parametrize('props,reason', [
    ({'Alpha': float('nan')}, 'opacity'),
    ({'RelativeScale3D': [1, 2, 1]}, 'nonuniform'),
    ({'CurrSkeletonData': {'ObjectPath': '/Game/Other'}}, 'current Spine'),
])
def test_unproven_render_controls_are_rejected(props, reason):
    source = layout()
    source['actors'][4]['props'].update(props)
    with pytest.raises(ValueError, match=reason):
        visible_layers(source)


def test_black_foreground_mask_is_not_silently_dropped():
    source = layout()
    source['actors'][4].update(kind="Class'UISprite'", props={
        'Color': [0, 0, 0, 255],
        'sprite': {'ObjectPath': '/Game/Aki/UI/UIResources/Common/Atlas/SP_CommonDefault'},
    })
    with pytest.raises(ValueError, match='Foreground UI masks'):
        visible_layers(source)


def test_tagged_struct_prefix_does_not_consume_next_native_map_entry():
    result, consumed = property_prefix(b'\x00\x00next', ['None'], [])
    assert result == {} and consumed == 2
