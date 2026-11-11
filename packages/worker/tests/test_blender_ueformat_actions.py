from types import SimpleNamespace

from wuwa_story_worker.blender_ueformat_actions import load_compatibility_check


def make_rig(offset):
    matrix = [[float(i == j) for j in range(4)] for i in range(4)]
    bone_matrix = [row[:] for row in matrix]
    bone_matrix[0][3] = offset
    bone = SimpleNamespace(name='root', parent=None, matrix_local=bone_matrix)
    class Bones(dict):
        def __iter__(self):
            return iter(self.values())
    return SimpleNamespace(data=SimpleNamespace(bones=Bones(root=bone)), matrix_world=matrix)


def test_small_ueformat_gltf_rounding_is_within_explicit_tolerance():
    assert load_compatibility_check()(make_rig(0), make_rig(1.4e-5), tolerance=2e-5) is None


def test_larger_rest_pose_difference_is_rejected():
    assert 'rest pose' in load_compatibility_check()(make_rig(0), make_rig(2.1e-5), tolerance=2e-5)
