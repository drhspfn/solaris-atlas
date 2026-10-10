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


def test_layered_bank_requires_verified_source_selection():
    objects = []
    for media_id in (101, 202, 303):
        obj = struct.pack("<II", media_id + 1, 0x00140001) + b"\x02" + struct.pack("<I", media_id)
        objects.append(b"\x02" + struct.pack("<I", len(obj)) + obj)
    hierarchy = struct.pack("<I", len(objects)) + b"".join(objects)
    data = (
        b"BKHD"
        + struct.pack("<II", 4, 172)
        + b"HIRC"
        + struct.pack("<I", len(hierarchy))
        + hierarchy
    )
    with pytest.raises(ValueError, match="single-source"):
        bank_media_id(data)
    assert bank_media_id(data, 101) == 101
    assert bank_media_id(data, 202) == 202
    with pytest.raises(ValueError, match="not present"):
        bank_media_id(data, 404)


def test_explicit_track_preserves_authored_muted_gain():
    from wuwa_story.ingestion.cutscenes import Soundtrack

    track = Soundtrack(bank="lyrics.bnk", media_id=123, gain_db=-99)
    assert track.model_dump()["gain_db"] == -99
    with pytest.raises(ValueError):
        Soundtrack(bank="lyrics.bnk", media_id=0)


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


def test_export_search_resolves_game_reference_casing(tmp_path):
    from wuwa_story_worker.cutscene_import import confined
    actual = tmp_path / "Client/Content/Aki/Movies/Example.MP4"
    actual.parent.mkdir(parents=True)
    actual.write_bytes(b"movie")
    assert confined(tmp_path, "client/content/aki/movies/example.mp4").read_bytes() == b"movie"
    assert movie_path(b"filepath://./Aki/Movies/Example.MP4\0").endswith("Example.MP4")


def test_unsupported_audio_bank_can_publish_partial_video_without_guessing(tmp_path):
    from wuwa_story.ingestion.cutscenes import VideoInput

    from wuwa_story_worker.cutscene_import import available_soundtracks
    (tmp_path / "layered.bnk").write_bytes(b"unsupported bank")
    video = VideoInput(asset="asset:ue:/Game/Video.Video", soundtrack=[{"bank": "layered.bnk", "language": "en"}])
    with pytest.raises(ValueError, match="Unsupported Wwise"):
        available_soundtracks(video, tmp_path, tmp_path)
    missing = []
    result = available_soundtracks(video, tmp_path, tmp_path, missing)
    assert result.soundtrack == []
    assert missing == ["layered.bnk: Unsupported Wwise bank version"]
    assert len(video.soundtrack) == 1
