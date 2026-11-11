import copy

import pytest

from wuwa_story_worker.blender_linked_scene import validate_manifest


def manifest():
    identity = [[float(i == j) for j in range(4)] for i in range(4)]
    return {'root_rig': 'Body', 'bone': 'Hand', 'child_root': 'Fan', 'evidence': 'Blueprint socket',
            'bone_to_anchor': identity, 'anchor_to_child': copy.deepcopy(identity)}


def test_valid_attachment_metadata():
    assert validate_manifest(manifest())['bone'] == 'Hand'


@pytest.mark.parametrize('field', ['root_rig', 'bone', 'child_root', 'evidence'])
def test_requires_verified_identifiers(field):
    item = manifest()
    item[field] = ''
    with pytest.raises(ValueError, match=field):
        validate_manifest(item)


def test_rejects_invalid_matrix():
    item = manifest()
    item['bone_to_anchor'][0][0] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        validate_manifest(item)
    item = manifest()
    item['anchor_to_child'][3][0] = 1
    with pytest.raises(ValueError, match='affine'):
        validate_manifest(item)
