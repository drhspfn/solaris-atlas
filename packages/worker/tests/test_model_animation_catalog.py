import json
import re
from pathlib import Path

import pytest

from wuwa_story_worker.model_animation_catalog import RULES, classify_animation


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Stand1_Action01", ("Idle & poses", "Idle", "Standing")),
        ("Cos_Pose", ("Idle & poses", "Poses", "Presentation")),
        ("AM_Attack02_limitdodge", ("Combat", "Attacks", "Ground")),
        ("AM_AirAttack_Start", ("Combat", "Attacks", "Aerial")),
        ("AM_Rogue_AirSkill01", ("Combat", "Skills", "Aerial")),
        ("AM_SkillQte01_Rogue", ("Combat", "Skills", "QTE")),
        ("AM_Death_InWater", ("Combat", "Reactions", "Death")),
        ("AM_Move_Limit_B", ("Movement", "Locomotion", "Move")),
        ("Walk_F", ("Movement", "Locomotion", "Walk")),
        ("Turn_R90D", ("Movement", "Locomotion", "Turn")),
        ("AM_Jump_Second_F", ("Traversal", "Jumping", "Jump")),
        ("FixHook_End_Fast", ("Traversal", "Grappling", "Hook")),
        ("Playpiano_01_Montage.005", ("Interactions", "Activities", "Music")),
        ("SitTalk01_Loop", ("Interactions", "Posture", "Sitting")),
        ("Touch03_End", ("Interactions", "Social", "Gestures")),
        ("Ui_Weapon_Start", ("Technical", "Presentation", "UI")),
        ("M0246_Cam025_R2T1ShouanrenMd10011", ("Technical", "Cinematics", "Camera")),
        ("Guidi01", ("Other", "Other", "Other")),
        ("NotAnAttack01", ("Other", "Other", "Other")),
        ("Runaway", ("Other", "Other", "Other")),
    ],
)
def test_authored_name_families(name, expected):
    result = classify_animation(name)
    assert result == dict(zip(("category", "group", "subgroup"), expected, strict=True), label=name)
    assert result == classify_animation(name)


@pytest.mark.parametrize("name", [None, 253, {}, [], "", "  "])
def test_malformed_names(name):
    assert classify_animation(name) == dict(
        category="Other", group="Other", subgroup="Other", label="Unnamed animation"
    )


def test_viewer_and_worker_declarative_rules_are_identical():
    source = (Path(__file__).resolve().parents[3] / "tools/model-viewer/animations.js").read_text()
    table = re.search(r"export const ANIMATION_CATALOG_RULES = (\[.*?\]);", source, re.S)[1]
    # JavaScript permits trailing commas; JSON does not.
    assert json.loads(re.sub(r",\s*]", "]", table)) == RULES
