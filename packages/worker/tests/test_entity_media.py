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
    assert event_media(cooked([media("en", 2)])) == {"en": 2}


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


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [("en", "ja", "ko", "zh"), ("en",)])
async def test_main_client_voice_ids_are_used_and_missing_languages_do_not_discard_audio(tmp_path, monkeypatch, languages):
    import hashlib
    import json
    import wave
    from unittest.mock import AsyncMock

    from wuwa_story_worker import entity_media

    root, voices = tmp_path / "client", tmp_path / "voices"
    root.mkdir()
    voices.mkdir()
    (root / "plan.json").write_text(json.dumps({"id": "client", "version": "3.7.0"}))
    (voices / "plan.json").write_text(json.dumps({"id": "voices", "version": "3.7.0"}))
    (voices / "status.json").write_text(json.dumps({"id": "voices", "state": "downloaded"}))
    fmodel, converter, decoder = [tmp_path / name for name in ("fmodel", "converter", "decoder")]
    fmodel.write_bytes(b"pinned extractor")
    source = "/Game/Aki/WwiseAudio/Events/play_favor_word_test.play_favor_word_test"
    asset = source.split(".")[0].removeprefix("/Game/")
    export = root / "exports" / hashlib.sha256(b"WwiseAudio").hexdigest()[:16]
    files = []
    for path in [asset + ".uasset", *[f"Aki/WwiseAudio_Generated/Media/{i}.wem" for i in range(1, len(languages) + 1)]]:
        relative = "Client/Content/" + path
        full = export / "files" / relative
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(path.encode())
        files.append({"path": relative, "sha256": hashlib.sha256(full.read_bytes()).hexdigest()})
    (export / "manifest.json").write_text(json.dumps({"job_id": "client", "extractor_sha256": hashlib.sha256(fmodel.read_bytes()).hexdigest(), "files": files}))
    cooked_root = root / "exports" / hashlib.sha256(asset.encode()).hexdigest()[:16] / "cooked-event"
    cooked_root.mkdir(parents=True)
    (cooked_root / "play_favor_word_test.json").write_text(json.dumps(cooked([media(lang, i) for i, lang in enumerate(languages, 1)])))
    monkeypatch.setattr(entity_media, "tool_path", lambda _: decoder)
    export_tool = AsyncMock(side_effect=AssertionError("No archive remount required"))
    monkeypatch.setattr(entity_media, "export_assets", export_tool)
    async def decode(args, log, timeout):
        assert args[0] == decoder
        with wave.open(str(args[3]), "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 80)
    tool = AsyncMock(side_effect=decode)
    monkeypatch.setattr(entity_media, "tool", tool)
    targets = [{"kind": "voice", "path": source, "language": language} for language in ("en", "ja", "ko", "zh")]
    resolved, missing = await entity_media.build_entity_files(root, targets, fmodel, converter, voice_root=voices)
    assert {row[0]["language"] for row in resolved} == set(languages)
    assert bool(missing) == (len(languages) < 4)
    assert tool.await_count == len(languages)
    # Retry reuses decoded audio, without touching the other language packages.
    await entity_media.build_entity_files(root, targets, fmodel, converter, voice_root=voices)
    assert tool.await_count == len(languages)
    export_tool.assert_not_awaited()


def test_cached_voice_bytes_must_match_manifest(tmp_path):
    from wuwa_story_worker.entity_media import verified_voice_export
    path = tmp_path / "770729442.wem"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        verified_voice_export((path, "0" * 64))
