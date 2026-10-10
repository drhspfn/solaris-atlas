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


def test_other_cutscene_single_and_gender_specific_timed_sound(tmp_path):
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
    single = plan_cutscene(db_path, tmp_path, "Other", "3.7.0")
    assert single["flow"]["nodes"][0]["kind"] == "clip"
    assert single["videos"][0]["soundtrack"] == []
    branch = plan_cutscene(db_path, tmp_path, "Branch", "3.7.0")
    assert branch["flow"]["nodes"][0]["kind"] == "choice"
    assert [option["rover"] for option in branch["flow"]["nodes"][0]["options"]] == [
        "female",
        "male",
    ]
    assert branch["videos"][0]["soundtrack"][0]["start_seconds"] == 2.5
    assert branch["videos"][0]["soundtrack"][0]["end_seconds"] == 10
    assert branch["videos"][1]["soundtrack"] == []


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
