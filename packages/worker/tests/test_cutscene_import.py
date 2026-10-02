import struct

import pytest

from wuwa_story_worker.cutscene_import import bank_media_id, movie_path, video_duration


def bank(object_type, obj):
    hierarchy = struct.pack("<I", 1) + bytes([object_type]) + struct.pack("<I", len(obj)) + obj
    return (
        b"BKHD"
        + struct.pack("<II", 4, 172)
        + b"HIRC"
        + struct.pack("<I", len(hierarchy))
        + hierarchy
    )


def test_exact_authored_movie_path():
    assert (
        movie_path(b"prefix filepath://./Aki/Movies/LevelA_Seq/M0206_Nvzhu.mp4\0")
        == "Client/Content/Aki/Movies/LevelA_Seq/M0206_Nvzhu.mp4"
    )
    with pytest.raises(ValueError):
        movie_path(b"filepath://./Aki/Movies/../secret.mp4\0")


def test_sound_and_music_source_layouts():
    sound = struct.pack("<II", 42, 0x00140001) + b"\x02" + struct.pack("<I", 988310690)
    music = struct.pack("<III", 42, 1, 0x00140001) + b"\x02" + struct.pack("<I", 633609166)
    assert bank_media_id(bank(2, sound)) == 988310690
    assert bank_media_id(bank(11, music)) == 633609166
    with pytest.raises(ValueError, match="Multiple music sources"):
        bank_media_id(
            bank(11, struct.pack("<III", 42, 2, 0x00140001) + b"\x02" + struct.pack("<I", 1))
        )


@pytest.mark.parametrize("data", [b"", b"BKHD", bank(2, b"x"), bank(2, b"x")[:-2]])
def test_malformed_banks_fail_closed(data):
    with pytest.raises(ValueError):
        bank_media_id(data)


def test_multiple_embedded_languages_are_not_silently_dropped(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "wuwa_story_worker.cutscene_import.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stderr=b"Duration: 00:01:00.00 Video: h264 Audio: aac Audio: aac"
        ),
    )
    with pytest.raises(ValueError, match="Multiple embedded audio"):
        video_duration(tmp_path / "ffmpeg", tmp_path / "movie.mp4")
