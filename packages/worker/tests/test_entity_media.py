import pytest

from wuwa_story_worker.entity_media import event_media


@pytest.mark.asyncio
async def test_absent_texture_packages_are_reported_without_failing_other_imports(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from wuwa_story_worker import entity_media
    monkeypatch.setattr(entity_media, "build_icons", AsyncMock(return_value={}))
    target = {"kind": "image", "path": "/Game/Aki/UI/Missing.Missing"}
    files, missing = await entity_media.build_entity_files(tmp_path, [target], tmp_path / "fmodel", tmp_path / "converter")
    assert files == [] and missing == [target["path"]]


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


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ambiguous", [("en",), ("en", "ja"), ("ja",)])
def test_variant_languages_do_not_discard_other_localizations(ambiguous, reverse):
    entries = []
    expected = {}
    for identity, language in enumerate(("en", "ja", "ko", "zh"), 1):
        if language in ambiguous:
            for offset, suffix in enumerate(("M", "F"), 10):
                value = media(language, identity * 100 + offset)
                value["DebugName"] = value["DebugName"].replace(".wav", f"_{suffix}.wav")
                entries.append(value)
        else:
            entries.append(media(language, identity))
            expected[language] = identity
    unresolved = {}
    assert event_media(cooked(entries[::-1] if reverse else entries), unresolved=unresolved) == expected
    assert set(unresolved) == set(ambiguous)
    assert all("authored selection or sequencing" in reason for reason in unresolved.values())


def test_conflicting_debug_name_only_blocks_its_own_language():
    unresolved = {}
    assert event_media(cooked([media("en", 2), media("en", 3), media("ja", 4)]),
                       unresolved=unresolved) == {"ja": 4}
    assert set(unresolved) == {"en"}
    assert "Conflicting media IDs" in unresolved["en"]


def test_all_random_variants_remain_unresolved_without_invented_order():
    entries = []
    for identity, language in enumerate(("en", "ja", "ko", "zh"), 1):
        for offset in (1, 2):
            value = media(language, identity * 10 + offset)
            value["DebugName"] = f"{language}_vo_fuluoluo_burst_burst0{offset}.wav"
            entries.append(value)
    unresolved = {}
    with pytest.raises(ValueError, match="authored sequencing"):
        event_media(cooked(entries), unresolved=unresolved)
    assert set(unresolved) == {"en", "ja", "ko", "zh"}


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
        [[source], map_icons._sha256(converter), map_icons._sha256(fmodel), "casefold_packages_v1"], sort_keys=True
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

    from wuwa_story_worker import character_voice_assets, entity_media

    root, voices = tmp_path / "client", tmp_path / "voices"
    root.mkdir()
    voices.mkdir()
    (root / "plan.json").write_text(json.dumps({"id": "client", "version": "3.7.0"}))
    (voices / "plan.json").write_text(json.dumps({"id": "voices", "version": "3.7.0"}))
    (voices / "status.json").write_text(json.dumps({"id": "voices", "state": "downloaded"}))
    fmodel, converter, decoder = [tmp_path / name for name in ("fmodel", "converter", "decoder")]
    fmodel.write_bytes(b"pinned extractor")
    converter.write_bytes(b"pinned converter")
    decoder.write_bytes(b"pinned decoder")
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
    monkeypatch.setattr(character_voice_assets, "export_assets", export_tool)
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


@pytest.mark.asyncio
async def test_spine_failure_log_has_context_without_exception_text(tmp_path, monkeypatch, caplog):
    from unittest.mock import AsyncMock

    from wuwa_story_worker import entity_media, story_spine

    target = {'owner': 42, 'path': 'spine_background:123', 'resource_id': 123, 'kind': 'spine'}
    monkeypatch.setattr(story_spine, 'build_spine_bundle', AsyncMock(side_effect=ValueError('secret token')))
    files, missing = await entity_media.build_entity_files(
        tmp_path, [target], tmp_path / 'fmodel', tmp_path / 'converter', run_id=7)

    assert files == [] and missing == [target['path']]
    assert 'story_spine.missing run=7 owner=42 resource=' in caplog.text
    assert 'stage=build error_type=ValueError' in caplog.text
    assert 'secret token' not in caplog.text and target['path'] not in caplog.text


def test_actionable_spine_control_missing_filters_diagnostic_only_items():
    from wuwa_story_worker.entity_media import actionable_spine_control_missing

    items = [
        {"status": "unsupported_control", "expected": "/Game/Aki/Sequence/Foo.1", "diagnostics": [{"code": "dynamic_operand"}]},
        {"status": "unresolved_audio", "code": "sequence_audio_unavailable"},
        {"status": "not_in_client", "expected": "/Game/NotInFinalPackage/EditorOnly"},
        {"status": "dependency_failed", "expected": "/Game/Aki/UI/RealMissing"},
    ]
    assert actionable_spine_control_missing(items) == [
        {"status": "dependency_failed", "expected": "/Game/Aki/UI/RealMissing"}
    ]
    assert actionable_spine_control_missing(items[:3]) == []


@pytest.mark.asyncio
async def test_entity_media_publish_retries_after_deadlock_with_rollback_and_ignores_diagnostic_controls(tmp_path, monkeypatch):
    import json
    import zipfile
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import DBAPIError

    from wuwa_story_worker import entity_media

    download_id = "a" * 64
    root = tmp_path / "client"
    root.mkdir()
    (root / "plan.json").write_text(json.dumps({"id": download_id, "version": "3.8.0", "tier": "hd"}))
    (root / "status.json").write_text(json.dumps({"id": download_id, "state": "downloaded"}))
    fmodel = tmp_path / "fmodel"
    converter = tmp_path / "converter"
    fmodel.write_bytes(b"fmodel")
    converter.write_bytes(b"converter")
    bundle = root / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("manifest.json", json.dumps({
            "animation_import": {"display": "static", "state": "static_only", "conversion_allowed": False, "reasons": []},
            "control_sources": {"missing": [
                {"status": "unsupported_control", "expected": "/Game/Aki/Sequence/Test.1"},
                {"status": "not_in_client", "expected": "/Game/NotInFinalPackage/EditorAsset"},
            ]},
        }))
    orig_b = root / "b.uasset"
    orig_a = root / "a.uasset"
    orig_b.write_bytes(b"bbb")
    orig_a.write_bytes(b"aaa")

    target = {
        "kind": "spine",
        "owner": 101,
        "release_id": 1,
        "path": "story_sequence:101",
        "language": None,
        "reference_type": "story_spine",
        "spine_recipe": "v1",
    }
    payload = {
        "schema_version": 1,
        "job_type": "media.entities",
        "run_id": 55,
        "request": {
            "asset_version": "3.8.0",
            "download_id": download_id,
            "tier": "hd",
            "game_version": "3.8.0",
            "entities": ["quest"],
            "kinds": ["image"],
        },
        "targets": [target],
    }
    run = SimpleNamespace(id=55, status="queued", error=None, metadata_json={"payload": payload}, raw_output=None, finished_at=None)

    commit_calls = 0
    rollback_calls = 0
    deadlock_orig = type("DeadlockOrig", (Exception,), {"sqlstate": "40P01"})()

    async def fake_commit():
        nonlocal commit_calls
        commit_calls += 1
        # Commit 1: status=running, Commit 2: after missing_media_targets, Commit 3: media_report,
        # Commit 4: first target publish -> simulate deadlock, Commit 5: retry succeeds, Commit 6: finalize
        if commit_calls == 4:
            raise DBAPIError("UPDATE storage.file_location", {}, deadlock_orig)

    async def fake_rollback():
        nonlocal rollback_calls
        rollback_calls += 1

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, ident):
            return run

        async def execute(self, *args, **kwargs):
            return None

        async def scalar(self, *args, **kwargs):
            return None

        commit = staticmethod(fake_commit)
        rollback = staticmethod(fake_rollback)

    registered_originals = []

    class FakeService:
        def __init__(self, storage):
            self.storage = storage

        async def prepare_file(self, path, mime=None):
            return SimpleNamespace(digest=path.read_bytes(), size=len(path.read_bytes()), stored=SimpleNamespace(etag="e"))

        async def register_file(self, session, path, kind, mime_type=None, prepared=None):
            if path in (orig_a, orig_b):
                registered_originals.append(path.name)
            return SimpleNamespace(id=1 if path == bundle else 2, sha256=b"x" * 32, size_bytes=3)

        async def register_variant(self, session, parent_id, child_id, vtype, run_id):
            return SimpleNamespace(metadata_json={})

        async def register_reference(self, session, **kwargs):
            return SimpleNamespace(id=9, file_id=kwargs["file_id"], metadata_json={})

    @contextmanager
    def fake_lock(_path):
        yield

    monkeypatch.setattr(entity_media, "validate_plan", lambda _: None)
    monkeypatch.setattr(entity_media, "workspace_lock", fake_lock)
    monkeypatch.setattr(entity_media, "SessionFactory", lambda bind=None: FakeSession())
    monkeypatch.setattr(entity_media, "S3Storage", lambda _s: SimpleNamespace(ensure_bucket=AsyncMock()))
    monkeypatch.setattr(entity_media, "FileRegistrationService", FakeService)
    monkeypatch.setattr(entity_media, "tool_path", lambda name: fmodel if "FMODEL" in name else converter)
    monkeypatch.setattr(entity_media, "resolve_soundtrack_targets", AsyncMock(return_value=[target]))
    monkeypatch.setattr(entity_media, "missing_media_targets", AsyncMock(return_value=[target]))
    monkeypatch.setattr(entity_media, "build_entity_files", AsyncMock(return_value=([(target, bundle, "unknown", "application/zip", [orig_b, orig_a])], [])))
    monkeypatch.setattr(entity_media, "publish_inventory", AsyncMock(return_value={"inventory": []}))
    monkeypatch.setattr(entity_media, "source_provenance", lambda *args, **kwargs: {"v": 1})
    monkeypatch.setattr(entity_media.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr("wuwa_story_worker.sequence_metadata.retained_bundle", AsyncMock(return_value=None))
    monkeypatch.setattr("wuwa_story_worker.sequence_fragments.publish_sequence_fragments", AsyncMock(return_value=None))
    monkeypatch.setattr("wuwa_story_worker.sequence_fragments.invalidate_sequence_quests", AsyncMock())
    monkeypatch.setattr("wuwa_story_worker.sequence_speaker_identity.repair_sequence_bundle_speakers", AsyncMock())

    await entity_media._process_entity_workspace(payload, object(), root)

    assert rollback_calls == 1
    assert commit_calls == 6
    assert run.status == "completed"
    assert run.raw_output["published"] == 1
    assert len(run.raw_output["incomplete_spine_controls"]) == 1
    # Originals were sorted deterministically by digest ('aaa' before 'bbb') on both attempts
    assert registered_originals == ["a.uasset", "b.uasset", "a.uasset", "b.uasset"]
