from wuwa_story_worker.map_catalog import mark_category


def test_authored_map_categories():
    assert mark_category("Resonance Beacon", "") == "teleport"
    assert mark_category("Tacet Field", "") == "tacet_field"
    assert mark_category("Cloudperch Seed", "") == "exploration"
    assert mark_category("Unknown", "IconMonsterHead001") == "boss"
    assert mark_category("Pioneer Association", "IconMapNpc") == "shop"
