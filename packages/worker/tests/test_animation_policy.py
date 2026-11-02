import json

import pytest

from wuwa_story_worker.animation_policy import assess_animation_sources


@pytest.mark.parametrize('extra,missing,allowed,reason', [
    ([], [], True, 'render_validation_required'),
    ([{'Type': 'LevelSequence'}], [], False, 'ui_composition_not_supported'),
    ([{'Type': 'MaterialInstanceConstant'}], [], False, 'material_semantics_unverified'),
    ([], [{'expected': '/Game/Page'}], False, 'missing_dependencies'),
])
def test_static_fallback_requires_explicit_render_validation(tmp_path, extra, missing, allowed, reason):
    (tmp_path / 'source.json').write_text(json.dumps([
        {'Type': 'SpineSkeletonDataAsset'}, {'Type': 'SpineAtlasAsset'}, *extra]))
    policy = assess_animation_sources({'/Game/Rig': {'decoded': tmp_path}}, missing, ['/Game/Rig'])
    assert policy['conversion_allowed'] is allowed
    assert policy['display'] == 'static'
    assert reason in {entry['code'] for entry in policy['reasons']}


def test_multiple_rigs_cannot_silently_become_one_animation(tmp_path):
    assets = {}
    for name in ('background', 'character'):
        decoded = tmp_path / name
        decoded.mkdir()
        (decoded / 'rig.json').write_text(json.dumps([{'Type': 'SpineSkeletonDataAsset'}, {'Type': 'SpineAtlasAsset'}]))
        assets['/Game/' + name] = {'decoded': decoded}
    result = assess_animation_sources(assets, [], list(assets))
    assert not result['conversion_allowed']
    assert 'composition_controls_required' in {entry['code'] for entry in result['reasons']}
