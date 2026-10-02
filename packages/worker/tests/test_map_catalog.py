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


def test_nearby_kites_use_template_icon_and_preserve_positions(tmp_path):
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
    for name, schema in schemas.items():
        with sqlite3.connect(tmp_path / name) as db:
            db.execute(schema)
            if name == "db_map.db":
                db.execute("CREATE TABLE multimap (BinData)")
    language = tmp_path / "en"
    language.mkdir()
    with sqlite3.connect(language / "lang_multi_text.db") as db:
        db.execute("CREATE TABLE MultiText (Id, Content)")
        db.execute("INSERT INTO MultiText VALUES (?, ?)", ("MapMark_15_MarkTitle", "Unclaimed Rafter Kite"))
    with sqlite3.connect(tmp_path / "db_map_mark.db") as db:
        db.execute("INSERT INTO mapmark VALUES (15, 8, 0, ?)", (blob({18: "MapMark_15_MarkTitle", 21: "/Game/Kite.Icon"}),))
    with sqlite3.connect(tmp_path / "db_template.db") as db:
        db.execute("INSERT INTO templateconfig VALUES (?, ?)", ("GameplayKite", blob({3: json.dumps({"BaseInfoComponent": {"MapIcon": 15}, "RewardComponent": None})})))
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
    assert marker["metadata_json"]["names"]["en"] == "Unclaimed Rafter Kite"
    assert marker["metadata_json"]["icon_source"] == "/Game/Kite.Icon"
    assert marker["metadata_json"]["hidden"] is False
    assert read_catalog(tmp_path, {912})[0] == []
