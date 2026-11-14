"""Shared validation for per-form animation pack descriptors."""

import re

MAX_ANIMATION_PACKS = 1024


def validate_animation_packs(packs):
    if not isinstance(packs, list) or len(packs) > MAX_ANIMATION_PACKS:
        raise ValueError("Invalid animation pack descriptors")
    ids, clips = set(), set()
    for pack in packs:
        if (not isinstance(pack, dict) or set(pack) != {"id", "key", "clips"}
                or not isinstance(pack["id"], str)
                or re.fullmatch(r"[0-9a-f]{16}", pack["id"]) is None
                or pack["key"] != "animation_pack_" + pack["id"]
                or not isinstance(pack["clips"], list) or not pack["clips"]
                or any(not isinstance(name, str) or not name.strip() for name in pack["clips"])):
            raise ValueError("Invalid animation pack descriptor")
        selected = pack["clips"]
        if pack["id"] in ids or len(set(selected)) != len(selected) or clips.intersection(selected):
            raise ValueError("Duplicate animation pack or selected clip")
        ids.add(pack["id"])
        clips.update(selected)
    return packs
