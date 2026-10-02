from wuwa_story_worker.map_catalog import mark_category


def test_authored_map_categories():
    assert mark_category("Resonance Beacon", "") == "teleport"
    assert mark_category("Tacet Field", "") == "tacet_field"
    assert mark_category("Cloudperch Seed", "") == "exploration"
    assert mark_category("Unknown", "IconMonsterHead001") == "boss"
    assert mark_category("Pioneer Association", "IconMapNpc") == "shop"


def test_combat_marks_are_not_generic_activities():
    assert mark_category("Tactical Hologram: Crownless", "IconActivity") == "hologram"
    assert mark_category("Dream Patrol: Twin Swords of Light", "IconAct") == "combat_activity"
