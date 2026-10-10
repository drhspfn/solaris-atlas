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


@pytest.mark.asyncio
async def test_image_cache_rejects_changed_source_bytes(tmp_path, monkeypatch):
    import hashlib
    import json
    from unittest.mock import AsyncMock

    from wuwa_story_worker import map_icons

    fmodel = tmp_path / "extractor"
    converter = tmp_path / "converter"
    fmodel.write_bytes(b"extractor")
    converter.write_bytes(b"converter")
    image = tmp_path / "image.png"
    image.write_bytes(b"published bytes")
    original = tmp_path / "source.uasset"
    original.write_bytes(b"original bytes")
    source = "/Game/Aki/UI/UIResources/Common/Image/IconA/Test.Test"
    markers = [{"metadata_json": {"icon_source": source}}]
    identity = json.dumps(
        [[source], map_icons._sha256(converter), map_icons._sha256(fmodel)], sort_keys=True
    )
    cache = (
        tmp_path / "entity-image-cache" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    )
    cache.parent.mkdir()
    icons = {source: {"path": str(image), "raw_paths": [str(original)]}}
    cache.write_text(
        json.dumps(
            {
                "icons": icons,
                "files": {
                    str(image): map_icons._sha256(image),
                    str(original): map_icons._sha256(original),
                },
            }
        ),
        encoding="utf-8",
    )
    export = AsyncMock(side_effect=RuntimeError("fresh extraction required"))
    monkeypatch.setattr(map_icons, "export_assets", export)
    assert (
        await map_icons.build_icons(tmp_path, fmodel, converter, markers, entity_media=True)
        == icons
    )
    export.assert_not_called()
    original.write_bytes(b"changed original")
    with pytest.raises(RuntimeError, match="fresh extraction"):
        await map_icons.build_icons(tmp_path, fmodel, converter, markers, entity_media=True)
    export.assert_awaited_once()


def test_dialect_variant_does_not_block_matching_localized_base_voice():
    entries = [media(language, index) for index, language in enumerate(("en", "ja", "ko", "zh"), 1)]
    canton = media("zh", 99)
    canton["DebugName"] = canton["DebugName"].replace(".wav", "_canton.wav")
    assert event_media(cooked([canton, *entries])) == {"zh": 4, "en": 1, "ja": 2, "ko": 3}
