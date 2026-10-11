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
            20: "Activity_111800001_Title", 23: "Activity_111800001_Desc",
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
        ("lang_multi_text.db", [("Activity_111800001_Title", "Blooms for the Shadow"),
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
