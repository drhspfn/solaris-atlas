"""Read authored NPC bone-visibility events without inferring animation state."""

import math
import re


def npc_bone_visibility_notifies(rows):
    """Return enabled hide-bone notifies and their sequence-relative times."""
    sequences = [row for row in rows if row.get("Type") == "AnimSequence"]
    if len(sequences) != 1:
        raise ValueError("Expected exactly one animation sequence")
    sequence = sequences[0]
    events = []
    for notify in sequence.get("Properties", {}).get("Notifies", []):
        if notify.get("NotifyName") != "TsAnimNotifyHideBone_C" or not notify.get(
            "bEnabled", True
        ):
            continue
        reference = notify.get("Notify", {}).get("ObjectName", "")
        match = re.fullmatch(r"TsAnimNotifyHideBone_C'([^':]+):([^']+)'", reference)
        if not match or match[1] != sequence.get("Name"):
            raise ValueError("Hide-bone notify does not reference this sequence")
        targets = [row for row in rows if row.get("Type") == "TsAnimNotifyHideBone_C"
                   and row.get("Name") == match[2]]
        if len(targets) != 1:
            raise ValueError("Hide-bone notify target is missing or ambiguous")
        properties = targets[0].get("Properties", {})
        bone, hide = properties.get("BoneName"), properties.get("Hide")
        time, chance = notify.get("LinkValue"), notify.get("NotifyTriggerChance")
        if (not isinstance(bone, str) or not bone or not isinstance(hide, bool)
                or isinstance(time, bool) or not isinstance(time, (int, float))
                or not math.isfinite(time) or time < 0
                or isinstance(chance, bool) or not isinstance(chance, (int, float))
                or not math.isfinite(chance) or not 0 <= chance <= 1):
            raise ValueError("Invalid hide-bone notify properties")
        events.append({"sequence": sequence["Name"], "time": time, "bone": bone,
                       "hide": hide, "chance": chance})
    return events
