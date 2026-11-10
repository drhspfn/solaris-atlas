from copy import deepcopy

import pytest

from wuwa_story_worker.npc_accessories import (
    CRISTOFORO_MODEL,
    npc_bone_visibility_notifies,
    visibility_label,
    visibility_sources,
)


def test_mask_control_uses_only_its_verified_mesh_and_bone():
    assert visibility_sources(CRISTOFORO_MODEL)[0].endswith('/Stand2')
    assert visibility_sources(CRISTOFORO_MODEL + '_Other') == []
    assert visibility_label(CRISTOFORO_MODEL, 'Bone_Piao027_M') == 'Mask'
    assert visibility_label('Other', 'Bone_Piao027_M') == 'Bone: Bone_Piao027_M'


@pytest.mark.parametrize("animation_type", ["AnimSequence", "AnimMontage"])
def test_cristoforo_maskoff_notify_is_an_event_not_a_guessed_default(animation_type):
    rows = [
        {"Type": animation_type, "Name": "Stand2", "Properties": {"Notifies": [{
            "NotifyName": "TsAnimNotifyHideBone_C", "bEnabled": True,
            "Notify": {"ObjectName": "TsAnimNotifyHideBone_C'Stand2:TsAnimNotifyHideBone_C_0'"},
            "LinkValue": 0.0, "NotifyTriggerChance": 1.0,
        }]}},
        {"Type": "TsAnimNotifyHideBone_C", "Name": "TsAnimNotifyHideBone_C_0",
         "Properties": {"BoneName": "Bone_Piao027_M", "Hide": True}},
    ]
    assert npc_bone_visibility_notifies(rows) == [{
        "sequence": "Stand2", "time": 0.0, "bone": "Bone_Piao027_M", "hide": True, "chance": 1.0,
    }]
    disabled = deepcopy(rows)
    disabled[0]["Properties"]["Notifies"][0]["bEnabled"] = False
    assert npc_bone_visibility_notifies(disabled) == []
    with pytest.raises(ValueError, match="missing or ambiguous"):
        npc_bone_visibility_notifies(rows[:1])
    wrong_sequence = deepcopy(rows)
    wrong_sequence[0]["Name"] = "Stand1"
    with pytest.raises(ValueError, match="does not reference"):
        npc_bone_visibility_notifies(wrong_sequence)
