import sqlite3

import flatbuffers
import pytest

from wuwa_story_worker.event_catalog import client_event_record, read_event_catalog, texture_path


def blob(fields, vector=None):
    builder = flatbuffers.Builder(256)
    offsets = {field: builder.CreateString(value) for field, value in fields.items()}
    if vector:
        builder.StartVector(4, len(vector), 4)
        for value in reversed(vector):
            builder.PrependInt32(value)
        offsets[16] = builder.EndVector()
    builder.StartObject(25)
    for field, offset in offsets.items():
        builder.PrependUOffsetTRelativeSlot(field, offset, 0)
    builder.PrependInt32Slot(1, 3 if vector else 33, 0)
    builder.Finish(builder.EndObject())
    return bytes(builder.Output())


def fixture_config(tmp_path):
    for filename, name, identifier, data in (
        ("db_activity.db", "activity", 102000009, blob({
            20: "Activity_InternalName", 21: "Activity_111800001_Title", 23: "Activity_111800001_Desc",
            24: "/Game/Aki/Artwork/Event.Event",
        })),
        ("db_gacha.db", "gachaviewinfo", 200087, blob({
            10: "/Game/Aki/Artwork/Weapon.Weapon",
        }, [21020107])),
    ):
        with sqlite3.connect(tmp_path / filename) as db:
            db.execute(f"CREATE TABLE {name} (Id INT, BinData BLOB)")
            db.execute(f"INSERT INTO {name} VALUES (?, ?)", (identifier, data))
    locale = tmp_path / "en"
    locale.mkdir()
    for filename, values in (
        ("lang_multi_text_1sthalf.db", [("WeaponConf_21020107_WeaponName", "Unspoken Rue")]),
        ("lang_multi_text.db", [("Activity_111800001_Title", "Blooms for the <color=#fff>Shadow</color>"),
                               ("Activity_111800001_Desc", "Earn <color=#fff>rewards</color>.")]),
    ):
        with sqlite3.connect(locale / filename) as db:
            db.execute("CREATE TABLE MultiText (Id TEXT, Content TEXT)")
            db.executemany("INSERT INTO MultiText VALUES (?, ?)", values)
    return tmp_path


def test_client_keys_override_numeric_id_and_filename_guesses(tmp_path):
    catalog = read_event_catalog(fixture_config(tmp_path))
    activity = client_event_record(catalog, "2:102000009")
    assert activity["locales"]["en"] == {
        "title": "Blooms for the Shadow", "description": "Earn rewards.",
    }
    assert activity["banner_path"] == "/Game/Aki/Artwork/Event"
    weapon = client_event_record(catalog, "1:200087")
    assert weapon["locales"]["en"]["title"] == "Unspoken Rue · Weapon Convene"
    assert weapon["featured_ids"] == [21020107]
    assert client_event_record(catalog, "2:999") is None


def test_missing_localization_is_reported_without_inventing_a_title(tmp_path):
    config = fixture_config(tmp_path)
    with sqlite3.connect(config / "en/lang_multi_text.db") as db:
        db.execute("DELETE FROM MultiText WHERE Id LIKE '%Title'")
    catalog = read_event_catalog(config)
    assert client_event_record(catalog, "2:102000009")["locales"] == {}


@pytest.mark.parametrize("path", ["/Game/../Secret", "Common/Event", ""])
def test_invalid_texture_paths_are_not_used(path):
    assert texture_path(path) is None


def test_weapon_selection_types_resolve_weapon_names_not_role_ids(tmp_path):
    config = fixture_config(tmp_path)
    with sqlite3.connect(config / "db_gacha.db") as db:
        builder = flatbuffers.Builder(128)
        name = builder.CreateString("/Game/Aki/UI/UIResources/Common/Image/Weapon")
        builder.StartVector(4, 1, 4)
        builder.PrependInt32(21020107)
        featured = builder.EndVector()
        builder.StartObject(18)
        builder.PrependInt32Slot(1, 14, 0)
        builder.PrependUOffsetTRelativeSlot(10, name, 0)
        builder.PrependUOffsetTRelativeSlot(16, featured, 0)
        builder.Finish(builder.EndObject())
        db.execute("UPDATE gachaviewinfo SET BinData=?", (bytes(builder.Output()),))
    record = read_event_catalog(config)["gacha:200087"]
    assert record["locales"]["en"]["title"] == "Unspoken Rue · Weapon Convene"
    assert record["event_kind"] == "banner"
    assert record["artwork"][0]["role"] == "background"


def test_secondary_images_follow_query_database_and_explicit_activity_foreign_key(tmp_path):
    config = fixture_config(tmp_path)
    readers = config / "readers/Config"
    readers.mkdir(parents=True)
    queries = readers.parent / "ConfigQuery"
    queries.mkdir()
    (readers / "Activity.js").write_text(
        'title(){var t=this.J7.__offset(this.z7,46);return t}desc(){var t=this.J7.__offset(this.z7,50);return t}'
        'bgresource(){var t=this.J7.__offset(this.z7,52);return t}', encoding="utf-8")
    (readers / "GachaViewInfo.js").write_text(
        'showidlist(t){var i=this.J7.__offset(this.z7,36);return i}'
        'underbgtexturepath(t){var i=this.J7.__offset(this.z7,24);return i}', encoding="utf-8")
    (readers / "EventPanel.js").write_text(
        'get ActivityId(){return this.activityid()}get Image(){return this.image()}'
        'activityid(){var t=this.J7.__offset(this.z7,6);return t?this.J7.readInt32(this.z7+t):0}'
        'image(t){var i=this.J7.__offset(this.z7,8);return i?this.J7.__string(this.z7+i,t):null}',
        encoding="utf-8")
    (queries / "EventPanelById.js").write_text('DB="db_panel.db",TABLE="EventPanel"', encoding="utf-8")
    builder = flatbuffers.Builder(128)
    path = builder.CreateString("/Game/Aki/UI/UIResources/UiActivity/Image/ActualBanner.ActualBanner")
    builder.StartObject(3)
    builder.PrependInt32Slot(1, 102000009, 0)
    builder.PrependUOffsetTRelativeSlot(2, path, 0)
    builder.Finish(builder.EndObject())
    with sqlite3.connect(config / "db_panel.db") as db:
        db.execute("CREATE TABLE eventpanel (Id INT, BinData BLOB)")
        db.execute("INSERT INTO eventpanel VALUES (?, ?)", (999, bytes(builder.Output())))
    # A stale table with the same name and incompatible layout is not read.
    with sqlite3.connect(config / "db_activity.db") as db:
        db.execute("CREATE TABLE eventpanel (Id INT, BinData BLOB)")
        db.execute("INSERT INTO eventpanel VALUES (999, ?)", (b"invalid stale layout",))
    record = read_event_catalog(config, readers)["102000009"]
    assert record["title_key"] == "Activity_111800001_Title"
    assert record["artwork"][-1]["path"].endswith("/ActualBanner")
    assert record["resources"][-1]["config_file"] == "db_panel.db"


def test_reward_names_follow_authored_item_keys_instead_of_item_id(tmp_path):
    config = fixture_config(tmp_path)
    builder = flatbuffers.Builder(256)
    title = builder.CreateString("Activity_111800001_Title")
    builder.StartObject(26)
    builder.PrependUOffsetTRelativeSlot(21, title, 0)
    builder.PrependInt32Slot(25, 99, 0)
    builder.Finish(builder.EndObject())
    with sqlite3.connect(config / "db_activity.db") as db:
        db.execute("UPDATE activity SET BinData=?", (bytes(builder.Output()),))
    builder = flatbuffers.Builder(128)
    builder.StartObject(2)
    builder.PrependInt32Slot(0, 44010029, 0)
    builder.PrependInt32Slot(1, 3, 0)
    item = builder.EndObject()
    builder.StartVector(4, 1, 4)
    builder.PrependUOffsetTRelative(item)
    items = builder.EndVector()
    builder.StartObject(5)
    builder.PrependUOffsetTRelativeSlot(4, items, 0)
    builder.Finish(builder.EndObject())
    with sqlite3.connect(config / "db_drop.db") as db:
        db.execute("CREATE TABLE droppackage (Id INT, BinData BLOB)")
        db.execute("INSERT INTO droppackage VALUES (99, ?)", (bytes(builder.Output()),))
    builder = flatbuffers.Builder(128)
    name = builder.CreateString("MonsterInfo_350000060_Name")
    builder.StartObject(3)
    builder.PrependUOffsetTRelativeSlot(2, name, 0)
    builder.Finish(builder.EndObject())
    with sqlite3.connect(config / "db_item.db") as db:
        db.execute("CREATE TABLE iteminfo (Id INT, BinData BLOB)")
        db.execute("INSERT INTO iteminfo VALUES (44010029, ?)", (bytes(builder.Output()),))
    with sqlite3.connect(config / "en/lang_multi_text.db") as db:
        db.execute("INSERT INTO MultiText VALUES ('MonsterInfo_350000060_Name', 'Elite Echo')")
    record = read_event_catalog(config)["102000009"]
    assert record["locales"]["en"]["rewards"] == [{"id": 44010029, "value": 3, "name": "Elite Echo"}]


def test_changed_primary_client_schema_fails_instead_of_reading_wrong_fields(tmp_path):
    config = fixture_config(tmp_path)
    readers = config / "readers"
    readers.mkdir()
    (readers / "Activity.js").write_text('title(){var t=this.J7.__offset(this.z7,48);return t}', encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported Activity.title"):
        read_event_catalog(config, readers)


def test_featured_weapon_images_follow_weaponconf_itemid(tmp_path):
    config = fixture_config(tmp_path)
    builder = flatbuffers.Builder(128)
    path = builder.CreateString("/Game/Aki/UI/UIResources/Common/Image/Weapons/ActualWeapon.ActualWeapon")
    builder.StartObject(25)
    builder.PrependUOffsetTRelativeSlot(23, path, 0)
    builder.Finish(builder.EndObject())
    with sqlite3.connect(config / "db_weapon.db") as db:
        db.execute("CREATE TABLE weaponconf (ItemId INT, BinData BLOB)")
        db.execute("INSERT INTO weaponconf VALUES (21020107, ?)", (bytes(builder.Output()),))
    record = read_event_catalog(config)["gacha:200087"]
    assert any(image["role"] == "featured_weapon" and image["path"].endswith("/ActualWeapon")
               for image in record["artwork"])
