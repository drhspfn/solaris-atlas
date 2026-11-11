from types import SimpleNamespace

from wuwa_story_worker.blender_scene_assembly import incompatible_skeletons


def rig(*, offset=0.0, parent=None, world_offset=0.0):
    def matrix(value):
        return [[float(i == j) + (value if i == 0 and j == 3 else 0) for j in range(4)] for i in range(4)]
    root = SimpleNamespace(name='root', parent=None, matrix_local=matrix(0))
    child = SimpleNamespace(name='child', parent=root if parent is None else parent, matrix_local=matrix(offset))
    class Bones(dict):
        def __iter__(self):
            return iter(self.values())
    return SimpleNamespace(data=SimpleNamespace(bones=Bones(root=root, child=child)), matrix_world=matrix(world_offset))


def test_compatible_rest_pose():
    assert incompatible_skeletons(rig(), rig()) is None


def test_rejects_changed_rest_pose_despite_same_names():
    assert 'rest pose' in incompatible_skeletons(rig(), rig(offset=0.01))


def test_rejects_changed_armature_transform():
    assert 'world transforms' in incompatible_skeletons(rig(), rig(world_offset=0.01))


def test_rejects_changed_parent():
    assert 'parent' in incompatible_skeletons(rig(), rig(parent=SimpleNamespace(name='other')))


def test_rejects_nonfinite_matrix():
    assert 'rest pose' in incompatible_skeletons(rig(), rig(offset=float('nan')))
