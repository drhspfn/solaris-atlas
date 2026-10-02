import pytest

from wuwa_story_worker.entity_media import event_media


def cooked(entries):
    return [{"EventCookedData": {"EventLanguageMap": [{"Value": {"Media": entries}}]}}]


def media(language, identity):
    return {
        "DebugName": f"Voices\\{language}\\role\\{language}_vo_changli_sys_to_player01.wav",
        "MediaId": identity,
        "MediaPathName": f"Media/{identity}.wem",
    }


def test_character_voice_languages_are_not_list_order():
    assert event_media(
        cooked([media("ko", 4), media("en", 2), media("zh", 1), media("ja", 3)])
    ) == {"ko": 4, "en": 2, "zh": 1, "ja": 3}


def test_missing_language_does_not_silently_fall_back():
    with pytest.raises(ValueError, match="four-language"):
        event_media(cooked([media("en", 2)]))


def test_multiple_clips_need_real_sequencing():
    with pytest.raises(ValueError, match="sequencing"):
        event_media(cooked([media("en", 2), media("en", 3)]))


def test_numeric_media_path_must_match_identity():
    value = media("en", 2)
    value["MediaPathName"] = "Media/other.wem"
    with pytest.raises(ValueError, match="identity"):
        event_media(cooked([value]))
