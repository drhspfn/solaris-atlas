import sqlite3

import flatbuffers
import pytest

from wuwa_story_worker.cutscene_import import confined
from wuwa_story_worker.cutscene_plan import plan_cutscene, sound_timing


def sound(path, start, end):
    builder = flatbuffers.Builder(100)
    value = builder.CreateString(path)
    builder.StartObject(6)
    builder.PrependUOffsetTRelativeSlot(3, value, 0)
    builder.PrependFloat32Slot(4, start, 0)
    builder.PrependFloat32Slot(5, end, -1)
    root = builder.EndObject()
    builder.Finish(root)
    return bytes(builder.Output())


@pytest.mark.parametrize("asset_version", ["3.7.0", "3.7.1"])
def test_other_cutscene_single_and_gender_specific_timed_sound(tmp_path, asset_version):
    db_path = tmp_path / "videos.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            "CREATE TABLE videodata (CgId, GirlOrBoy, CgName, BinData); CREATE TABLE videosound (CaptionId, GirlOrBoy, CgName, BinData);"
        )
        db.executemany(
            "INSERT INTO videodata VALUES (?, ?, ?, ?)",
            [
                (501, 2, "Other", b"/Game/Movies/Other.Other\0"),
                (502, 0, "Branch", b"/Game/Movies/Female.Female\0"),
                (503, 1, "Branch", b"/Game/Movies/Male.Male\0"),
            ],
        )
        db.execute(
            "INSERT INTO videosound VALUES (?, ?, ?, ?)",
            (1, 0, "Branch", sound("/Game/Audio/event.event", 2.5, 10)),
        )
    (tmp_path / "event.bnk").write_bytes(b"bank")
    single = plan_cutscene(db_path, tmp_path, "Other", asset_version)
    assert single["asset_version"] == asset_version
    assert single["flow"]["nodes"][0]["kind"] == "clip"
    assert single["videos"][0]["soundtrack"] == []
    branch = plan_cutscene(db_path, tmp_path, "Branch", asset_version)
    assert branch["flow"]["nodes"][0]["kind"] == "choice"
    assert [option["rover"] for option in branch["flow"]["nodes"][0]["options"]] == [
        "female",
        "male",
    ]
    assert branch["videos"][0]["soundtrack"][0]["start_seconds"] == 2.5
    assert branch["videos"][0]["soundtrack"][0]["end_seconds"] == 10
    assert branch["videos"][1]["soundtrack"] == []


@pytest.mark.parametrize("asset_version", ["3.6.0", "3.8.0", "4.0.0", "3.7.2", "3.7"])
def test_unverified_client_schema_is_rejected_before_opening_database(tmp_path, asset_version):
    with pytest.raises(ValueError, match="Config schema is verified"):
        plan_cutscene(tmp_path / "absent.db", tmp_path, "Other", asset_version)
    assert not (tmp_path / "absent.db").exists()


def test_export_path_cannot_escape(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        confined(tmp_path, "../secret")


def test_non_finite_source_timing_rejected():
    with pytest.raises(ValueError, match="Non-finite"):
        sound_timing(sound("/Game/Audio/event.event", float("nan"), -1))


def test_shared_video_assets_keep_choice_without_duplicate_publication(tmp_path):
    db_path = tmp_path / "videos.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("CREATE TABLE videodata (CgId, GirlOrBoy, CgName, BinData); CREATE TABLE videosound (CaptionId, GirlOrBoy, CgName, BinData);")
        db.executemany("INSERT INTO videodata VALUES (?, ?, ?, ?)", [(1, 0, "Shared", b"/Game/Movies/Shared.Shared\0"), (2, 1, "Shared", b"/Game/Movies/Shared.Shared\0")])
    recipe = plan_cutscene(db_path, tmp_path, "Shared", "3.7.0")
    assert len(recipe["videos"]) == 1
    assert len(recipe["flow"]["nodes"][0]["options"]) == 2
    assert not recipe["compare_variants"]


def test_shared_sound_list_replaces_gender_specific_rows(tmp_path):
    db_path = tmp_path / "videos.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("CREATE TABLE videodata (CgId, GirlOrBoy, CgName, BinData); CREATE TABLE videosound (CaptionId, GirlOrBoy, CgName, BinData);")
        db.execute("INSERT INTO videodata VALUES (?, ?, ?, ?)",
                   (1, 0, "Shared", b"/Game/Movies/Shared.Shared\0"))
        db.executemany("INSERT INTO videosound VALUES (?, ?, ?, ?)", [
            (1, 2, "Shared", sound("/Game/Audio/common.common", 0, -1)),
            (2, 2, "Shared", sound("/Game/Audio/later.later", 3, -1)),
            (3, 0, "Shared", sound("/Game/Audio/female.female", 0, -1)),
        ])
    for name in ("common", "later"):
        (tmp_path / f"{name}.bnk").write_bytes(b"bank")
    missing = []
    recipe = plan_cutscene(db_path, tmp_path, "Shared", "3.7.1", missing_assets=missing)
    assert [track["bank"] for track in recipe["videos"][0]["soundtrack"]] == ["common.bnk", "later.bnk"]
    assert missing == []  # The unused female bank is not a missing dependency.


def test_shared_movie_keeps_two_rover_soundtracks_without_duplicate_video(tmp_path):
    db_path = tmp_path / "videos.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("CREATE TABLE videodata (CgId, GirlOrBoy, CgName, BinData); CREATE TABLE videosound (CaptionId, GirlOrBoy, CgName, BinData);")
        db.executemany("INSERT INTO videodata VALUES (?, ?, ?, ?)", [
            (235, 1, "C0015", b"/Game/Movies/C0015.C0015\0"),
            (236, 0, "C0015", b"/Game/Movies/C0015.C0015\0"),
        ])
        db.execute("INSERT INTO videosound VALUES (?, ?, ?, ?)",
                   (78, 2, "C0015", sound("/Game/Audio/event.event", 0, -1)))
    (tmp_path / "event.bnk").write_bytes(b"bank")
    recipe = plan_cutscene(db_path, tmp_path, "C0015", "3.7.1")
    assert len(recipe["videos"]) == 1
    assert [track["rover"] for track in recipe["videos"][0]["soundtrack"]] == ["male", "female"]
    assert [node["rover"] for node in recipe["flow"]["nodes"] if node["kind"] == "clip"] == ["male", "female"]
    assert not recipe["compare_variants"]


def test_missing_bank_is_reported_for_partial_video_and_case_is_resolved(tmp_path):
    db_path = tmp_path / "videos.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("CREATE TABLE videodata (CgId, GirlOrBoy, CgName, BinData); CREATE TABLE videosound (CaptionId, GirlOrBoy, CgName, BinData);")
        db.execute("INSERT INTO videodata VALUES (?, ?, ?, ?)", (1, 2, "Shared", b"/Game/Movies/Shared.Shared\0"))
        db.execute("INSERT INTO videosound VALUES (?, ?, ?, ?)", (1, 2, "Shared", sound("/Game/Audio/Event.Event", 0, -1)))
    missing = []
    recipe = plan_cutscene(db_path, tmp_path, "Shared", "3.7.0", missing_assets=missing)
    assert missing == ["Event"]
    assert recipe["videos"][0]["soundtrack"] == []
    (tmp_path / "event.BNK").write_bytes(b"bank")
    assert len(plan_cutscene(db_path, tmp_path, "Shared", "3.7.0")["videos"][0]["soundtrack"]) == 1


def test_localized_banks_are_preserved_as_four_separate_tracks(tmp_path):
    db_path = tmp_path / "videos.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("CREATE TABLE videodata (CgId, GirlOrBoy, CgName, BinData); CREATE TABLE videosound (CaptionId, GirlOrBoy, CgName, BinData);")
        db.execute("INSERT INTO videodata VALUES (?, ?, ?, ?)", (1, 2, "Shared", b"/Game/Movies/Shared.Shared\0"))
        db.execute("INSERT INTO videosound VALUES (?, ?, ?, ?)", (1, 2, "Shared", sound("/Game/Audio/Event.Event", 1, -1)))
    for language in ("en", "ja", "ko", "zh"):
        directory = tmp_path / "Event" / language
        directory.mkdir(parents=True)
        (directory / "event.bnk").write_bytes(b"bank")
    recipe = plan_cutscene(db_path, tmp_path, "Shared", "3.7.0")
    tracks = recipe["videos"][0]["soundtrack"]
    assert {track["language"] for track in tracks} == {"en", "ja", "ko", "zh"}
    assert all(track["start_seconds"] == 1 for track in tracks)
    (tmp_path / "Event/ko/event.bnk").unlink()
    with pytest.raises(ValueError, match="ambiguous localized"):
        plan_cutscene(db_path, tmp_path, "Shared", "3.7.0")
