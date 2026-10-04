from wuwa_story.api.routes.story.captions import caption_tracks


def test_frame_units_and_voice_specific_timing():
    rows = [
        {
            "CaptionText": "a",
            "ShowMoment": 30,
            "Duration": 60,
            "ShowMomentEn": 60,
            "DurationEn": 30,
        },
        {"CaptionText": "b", "ShowMoment": 100, "Duration": 10, "DurationEn": 0},
    ]
    tracks = caption_tracks(rows, {"a": "Hello", "b": "World"})
    assert [(c["start"], c["end"]) for c in tracks["en"]] == [(2, 3)]
    assert tracks["ja"][0]["start"] == 1
    assert len(tracks["ja"]) == 2


def test_missing_text_and_invalid_ranges_are_not_shown():
    rows = [
        {"CaptionText": "missing", "Duration": 30},
        {"CaptionText": "a", "ShowMoment": -1, "Duration": 30},
        {"CaptionText": "a", "Duration": 0},
    ]
    assert all(not cues for cues in caption_tracks(rows, {"a": "Text"}).values())
