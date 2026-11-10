import pytest

from wuwa_story_worker.model_sources import spring_profile


@pytest.mark.parametrize(
    ("axis", "expected"),
    [
        ("X_Positive", [1, 0, 0]),
        ("X_Negative", [-1, 0, 0]),
        ("Y_Positive", [0, 0, 1]),
        ("Y_Negative", [0, 0, -1]),
        ("Z_Positive", [0, 1, 0]),
        ("Z_Negative", [0, -1, 0]),
    ],
)
def test_authored_axis_to_gltf_joint_space(axis, expected):
    node = {
        "RootBones": [{"BoneName": "ZSpring_Spine_O01_R"}],
        "BoneForwardAxis": "EBoneForwardAxis::" + axis,
        "DummyBoneLength": 5.0,
        "PhysicsSettings": {"Damping": 0.2, "Stiffness": 0.65, "LimitAngle": 20},
    }
    result = spring_profile([{"Name": "Default__PC", "Properties": {"Kawaii": node}}], "PC", "sha")
    assert result["format"] == "solaris-spring-preview-v1"
    assert result["groups"][0]["dummyAxis"] == expected
    assert result["groups"][0]["dummyLength"] == 0.05


@pytest.mark.parametrize("axis", [None, "Y_Positive", "EBoneForwardAxis::W_Positive", [], 1])
def test_invalid_axis_is_rejected(axis):
    node = {"RootBones": [{"BoneName": "root"}], "PhysicsSettings": {"Damping": 0.2},
            "BoneForwardAxis": axis}
    with pytest.raises(ValueError, match="axis"):
        spring_profile([{"Name": "Default__PC", "Properties": {"Kawaii": node}}], "PC", "sha")
