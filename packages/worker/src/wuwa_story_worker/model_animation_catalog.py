"""Deterministic name-family catalog; never infer character or animation identity.

Keep RULES in sync with ANIMATION_CATALOG_RULES in tools/model-viewer/animations.js.
The parity test checks the declarative tables without coupling either runtime.
"""

import re

# Ordered, case-sensitive rules grounded in authored animation names.
RULES = [
    ["^[DM][0-9]+_Cam", "Technical", "Cinematics", "Camera"],
    ["^Ui_", "Technical", "Presentation", "UI"],
    ["^(?:Cos_Pose|Team_Stand)(?:[0-9]|_|$)", "Idle & poses", "Poses", "Presentation"],
    ["^AirAttack(?:[0-9]|_|$)", "Combat", "Attacks", "Aerial"],
    ["^Attack(?:L)?(?:[0-9]|_|$)", "Combat", "Attacks", "Ground"],
    ["^AirSkill(?:[0-9]|_|$)", "Combat", "Skills", "Aerial"],
    ["^SkillQte(?:[0-9]|_|$)", "Combat", "Skills", "QTE"],
    ["^Skill(?:[0-9]|_|$)", "Combat", "Skills", "Ground"],
    ["^Burst(?:[0-9]|_|$)", "Combat", "Skills", "Burst"],
    ["^Execute(?:[0-9]|_|$)", "Combat", "Attacks", "Execution"],
    ["^Death(?:[0-9]|_|$)", "Combat", "Reactions", "Death"],
    ["^(?:Hit|Hurt)(?:[0-9]|_|$)", "Combat", "Reactions", "Hit"],
    ["^Dodge(?:[0-9]|_|$)", "Combat", "Evasion", "Dodge"],
    ["^Walk(?:[0-9]|_|$)", "Movement", "Locomotion", "Walk"],
    ["^Run(?:[0-9]|_|$)", "Movement", "Locomotion", "Run"],
    ["^Sprint(?:[0-9]|_|$)", "Movement", "Locomotion", "Sprint"],
    ["^Swim(?:[0-9]|_|$)", "Movement", "Locomotion", "Swim"],
    ["^Move(?:[0-9]|_|$)", "Movement", "Locomotion", "Move"],
    ["^Turn(?:[0-9]|_|$)", "Movement", "Locomotion", "Turn"],
    ["^Jump(?:[0-9]|_|$)", "Traversal", "Jumping", "Jump"],
    ["^(?:Fall|Land)(?:[0-9]|_|$)", "Traversal", "Jumping", "Fall & land"],
    ["^Climb(?:[0-9]|_|$)", "Traversal", "Climbing", "Climb"],
    ["^(?:Glide|Fly)(?:[0-9]|_|$)", "Traversal", "Flight", "Glide"],
    ["^FixHook(?:[0-9]|_|$)", "Traversal", "Grappling", "Hook"],
    ["^(?:Stand|Idle)(?:[0-9]|_|$|Change)", "Idle & poses", "Idle", "Standing"],
    ["^Sit(?:[0-9]|_|$|Promise|Push|Talk)", "Interactions", "Posture", "Sitting"],
    ["^Sleeping(?:[0-9]|_|$)", "Idle & poses", "Rest", "Sleeping"],
    ["^Squat(?:[0-9]|_|$)", "Interactions", "Posture", "Squatting"],
    ["^(?:Playpiano|Tanqin)(?:[0-9]|_|$)", "Interactions", "Activities", "Music"],
    ["^Drink(?:[0-9]|_|$)", "Interactions", "Activities", "Drinking"],
    ["^(?:Talk|Greetings)(?:[0-9]|_|$)", "Interactions", "Social", "Conversation"],
    [
        "^(?:Awareness|Elevate|Expect|Fool|Promise|Shy|Touch)(?:[0-9]|_|$)",
        "Interactions",
        "Social",
        "Gestures",
    ],
]


def classify_animation(name):
    """Return presentation metadata without changing the authored clip name."""
    label = name if isinstance(name, str) and name.strip() else "Unnamed animation"
    family = label
    if family.startswith("AM_"):
        family = family[3:]
    if family.startswith("Rogue_"):
        family = family[6:]
    for pattern, category, group, subgroup in RULES:
        if re.search(pattern, family):
            return dict(category=category, group=group, subgroup=subgroup, label=label)
    return dict(category="Other", group="Other", subgroup="Other", label=label)
