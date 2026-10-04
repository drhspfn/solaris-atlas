import pytest

from wuwa_story_worker.map_catalog import collection_type, mark_category


def test_authored_map_categories():
    assert mark_category("Resonance Beacon", "") == "teleport"
    assert mark_category("Tacet Field", "") == "tacet_field"
    assert mark_category("Cloudperch Seed", "") == "exploration"
    assert mark_category("Unknown", "IconMonsterHead001") == "boss"
    assert mark_category("Pioneer Association", "IconMapNpc") == "shop"


def test_combat_marks_are_not_generic_activities():
    assert mark_category("Tactical Hologram: Crownless", "IconActivity") == "hologram"
    assert mark_category("Dream Patrol: Twin Swords of Light", "IconAct") == "combat_activity"


@pytest.mark.parametrize("degree,item_id,name", [
    (None, None, "Unclaimed Rafter Kite"),
    (8, 40040001, "Sonance Casket"),
    (16, 40040002, "Windchimer"),
    (34, 40040004, "Sonance Casket: Ragunna"),
    (42, 40040005, "Sonance Casket: Septimont"),
    (60, 40040006, "Tape of Last Words"),
    (87, 40040008, "Unclaimed Rafter Kite"),
    (7, None, "Blobfly"),
    (17, None, "Frostbug"),
])
def test_nearby_collectibles_use_source_names_and_preserve_positions(tmp_path, degree, item_id, name):
    import json
    import sqlite3

    import flatbuffers

    from wuwa_story_worker.map_catalog import read_catalog

    def blob(fields, size=30):
        builder = flatbuffers.Builder(256)
        offsets = {key: builder.CreateString(value) for key, value in fields.items() if isinstance(value, str)}
        builder.StartObject(size)
        for key, value in fields.items():
            if key in offsets:
                builder.PrependUOffsetTRelativeSlot(key, offsets[key], 0)
            else:
                builder.PrependInt32Slot(key, value, 0)
        builder.Finish(builder.EndObject())
        return bytes(builder.Output())

    schemas = {
        "db_area.db": "CREATE TABLE area (AreaId, Level, BinData)",
        "db_item.db": "CREATE TABLE iteminfo (Id, BinData)",
        "db_template.db": "CREATE TABLE templateconfig (BlueprintType, BinData)",
        "db_drop.db": "CREATE TABLE droppackage (Id, BinData)",
        "db_enrichment.db": "CREATE TABLE enrichmentareaconfig (BinData)",
        "db_monster_Info.db": "CREATE TABLE monsterinfo (BinData)",
        "db_level_entity.db": "CREATE TABLE levelentityconfig (Id, MapId, EntityId, BlueprintType, BinData)",
        "db_map_mark.db": "CREATE TABLE mapmark (MarkId, MapId, EntityConfigId, BinData)",
        "db_map.db": "CREATE TABLE akimap (BinData)",
    }
    for filename, schema in schemas.items():
        with sqlite3.connect(tmp_path / filename) as db:
            db.execute(schema)
            if filename == "db_map.db":
                db.execute("CREATE TABLE multimap (BinData)")
    language = tmp_path / "en"
    language.mkdir()
    label_key = "ExploreProgress_test_TypeName" if degree in (7, 17) else "MapMark_test_MarkTitle"
    with sqlite3.connect(language / "lang_multi_text.db") as db:
        db.execute("CREATE TABLE MultiText (Id, Content)")
        db.execute("INSERT INTO MultiText VALUES (?, ?)", (label_key, name))
        db.execute("INSERT INTO MultiText VALUES (?, ?)", ("ItemInfo_test_Name", name))
    if item_id:
        with sqlite3.connect(tmp_path / "db_item.db") as db:
            db.execute("INSERT INTO iteminfo VALUES (?, ?)", (item_id, blob({2: "ItemInfo_test_Name", 8: "/Game/Item.Icon"})))
    with sqlite3.connect(tmp_path / "db_map_mark.db") as db:
        mark_id = {7: 11, 17: 12}.get(degree, 15)
        db.execute("INSERT INTO mapmark VALUES (?, 8, 0, ?)", (mark_id, blob({18: label_key, 21: "/Game/Kite.Icon"})))
    if degree == 17:
        with sqlite3.connect(tmp_path / "db_monster_Info.db") as db:
            db.execute("INSERT INTO monsterinfo VALUES (?)", (blob({7: "GameplayKite", 3: "/Game/Monster.Icon"}),))
    with sqlite3.connect(tmp_path / "db_template.db") as db:
        components = {"BaseInfoComponent": {"MapIcon": 15} if degree is None else {"Category": {"ExploratoryDegree": degree}},
                      "RewardComponent": {"RewardType": 1, "RewardId": 1016} if degree else None,
                      "CollectComponent": {"Disabled": True}}
        db.execute("INSERT INTO templateconfig VALUES (?, ?)", ("GameplayKite", blob({3: json.dumps(components)})))
    builder = flatbuffers.Builder(256)
    builder.StartObject(3)
    builder.PrependInt32Slot(0, -123400, 0)
    builder.PrependInt32Slot(1, 567800, 0)
    position = builder.EndObject()
    builder.StartVector(4, 1, 4)
    builder.PrependUOffsetTRelative(position)
    positions = builder.EndVector()
    builder.StartObject(11)
    builder.PrependUOffsetTRelativeSlot(9, positions, 0)
    builder.Finish(builder.EndObject())
    with sqlite3.connect(tmp_path / "db_level_entity.db") as db:
        db.execute("INSERT INTO levelentityconfig VALUES (1, 8, 123, 'GameplayKite', ?)", (bytes(builder.Output()),))
    markers, _, _ = read_catalog(tmp_path, {8})
    assert len(markers) == 1  # The type-only MapMark must not create another placement.
    marker = markers[0]
    assert (marker["entity_id"], marker["world_x"], marker["world_y"]) == (123, -1234, 5678)
    assert marker["category"] == "collectible"
    assert marker["metadata_json"]["names"]["en"] == name
    assert marker["metadata_json"]["icon_source"] == ("/Game/Item.Icon" if item_id else "/Game/Kite.Icon")
    assert "drop_item_ids" not in marker["metadata_json"]  # Server drop plans aren't client previews.
    if degree:
        assert marker["metadata_json"]["type_key"].startswith("collection:")
    assert marker["metadata_json"]["hidden"] is False
    assert read_catalog(tmp_path, {912})[0] == []


def test_collection_identity_rejects_props_and_honors_component_overrides():
    template = {"BaseInfoComponent": {"MapIcon": 7, "Category": {"ExploratoryDegree": 34}},
                "RewardComponent": {"RewardType": 1, "RewardId": 1096}}
    assert collection_type(template, {})[0] == "sonance_casket_ragunna"
    assert collection_type(template, {"RewardComponent": {"Disabled": True}}) is None
    assert collection_type(template, {"RewardComponent": {"RewardType": 2}}) is None
    assert collection_type(template, {"BaseInfoComponent": {"Category": {}}}) is None
    assert collection_type({"BaseInfoComponent": template["BaseInfoComponent"]}, {}) is None
    assert collection_type({"BaseInfoComponent": {"MapIcon": 7}, "RewardComponent": template["RewardComponent"]}, {}) is None
    for degree, kind in [(7, "blobfly"), (17, "frostbug")]:
        assert collection_type(template, {"BaseInfoComponent": {"Category": {"ExploratoryDegree": degree}}})[0] == kind


def test_reward_preview_uses_placement_override_and_never_guesses_unknown_reward_type():
    from wuwa_story_worker.map_catalog import reward_preview
    template = {"RewardType": 0, "RewardId": 10}
    assert reward_preview(template, {"RewardId": 20}, {10: [1], 20: [2, 2, 3]})["drop_item_ids"] == [2, 3]
    assert reward_preview(template, {"Disabled": True}, {10: [1]}) == {}
    assert reward_preview(template, {"RewardType": 99}, {10: [1]}) == {}
