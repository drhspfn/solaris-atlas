import asyncio
import copy
import json
import wave
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from wuwa_story.ingestion.entity_media import MediaRequest

from wuwa_story_worker import entity_media
from wuwa_story_worker.media_provenance import temporary_entity_workspace


def request():
    return MediaRequest(download_id="a" * 64, asset_version="3.7.0", tier="hd",
                        game_version="1.1.0", entities=["character:1"], kinds=["image"])


def client(tmp_path):
    value = request()
    root = tmp_path / "assets" / f"3.7.0-hd-{value.download_id[:16]}"
    (root / "game").mkdir(parents=True)
    (root / "game" / "retained.pak").write_bytes(b"compressed client")
    (root / "plan.json").write_text(json.dumps({"id": value.download_id, "version": "3.7.0", "tier": "hd"}))
    (root / "status.json").write_text(json.dumps({"id": value.download_id, "state": "downloaded"}))
    (root / "keys.txt").write_text("private test keys")
    return root


@pytest.mark.parametrize("fail", [False, True])
def test_standalone_scratch_is_removed_and_archives_survive(tmp_path, fail):
    root = client(tmp_path)
    scratch = None
    try:
        with temporary_entity_workspace(root) as scratch:
            assert (scratch / "game" / "retained.pak").read_bytes() == b"compressed client"
            (scratch / "export.uasset").write_bytes(b"extracted")
            if fail:
                raise RuntimeError("publication failed")
    except RuntimeError:
        assert fail
    assert not scratch.exists()
    assert (root / "game" / "retained.pak").read_bytes() == b"compressed client"
    assert not (root / "export.uasset").exists()


@pytest.mark.asyncio
async def test_cancellation_waits_for_publication_before_cleanup(tmp_path, monkeypatch):
    root = client(tmp_path)
    monkeypatch.setattr(entity_media, "asset_workspace", lambda: tmp_path)
    started, finish = asyncio.Event(), asyncio.Event()
    roots = []

    async def publish(payload, connection, scratch):
        roots.append(scratch)
        started.set()
        await finish.wait()
        assert scratch.exists()
        (scratch / "published").write_text("done")

    monkeypatch.setattr(entity_media, "_process_entity_workspace", publish)
    task = asyncio.create_task(entity_media._process_entity_media({"request": request().model_dump()}, None))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and roots[0].exists()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not roots[0].exists()
    assert (root / "game" / "retained.pak").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [True, False])
async def test_worker_exports_only_missing_and_persists_game_identity(tmp_path, monkeypatch, published):
    root = client(tmp_path)
    value = request()
    target = {"owner": 1, "release_id": 2, "path": "/Game/Icon.Icon", "kind": "image", "language": None}
    payload = {"request": value.model_dump(), "run_id": 7, "targets": [target]}
    run = SimpleNamespace(id=7, status="queued", metadata_json={"payload": payload})
    session = AsyncMock()
    session.get.return_value = run
    session.scalar.return_value = None
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(entity_media, "SessionFactory", factory)
    monkeypatch.setattr(entity_media, "validate_plan", lambda _: None)
    monkeypatch.setattr(entity_media, "missing_media_targets", AsyncMock(return_value=[] if published else [target]))
    monkeypatch.setattr(entity_media, "publish_inventory", AsyncMock(return_value={}))
    storage = AsyncMock()
    monkeypatch.setattr(entity_media, "S3Storage", lambda _: storage)
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=11)
    variant, reference = SimpleNamespace(metadata_json={}), SimpleNamespace()
    service.register_variant.return_value = variant
    service.register_reference.return_value = reference
    monkeypatch.setattr(entity_media, "FileRegistrationService", lambda _: service)
    executable = tmp_path / "tool"
    executable.write_bytes(b"pinned tool version")
    monkeypatch.setattr(entity_media, "tool_path", lambda _: executable)
    raw = root / "exports" / "files" / "Client" / "Content" / "Icon.uasset"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"source package")
    image = root / "decoded.png"
    image.write_bytes(b"image")
    build = AsyncMock(return_value=([(target, image, "image", "image/png", [raw])], []))
    monkeypatch.setattr(entity_media, "build_entity_files", build)
    await entity_media._process_entity_workspace(payload, None, root)
    assert run.status == "completed"
    assert run.raw_output["skipped"] == int(published)
    if published:
        build.assert_not_awaited()
        service.register_file.assert_not_awaited()
    else:
        assert build.await_args.args[1] == [target]
        provenance = reference.metadata_json["game_source"]
        assert provenance["download_id"] == value.download_id
        assert provenance["package_path"] == "/Game/Icon"
        assert provenance["source_files"][0]["package_path"] == "Client/Content/Icon.uasset"
        assert len(provenance["source_files"][0]["sha256"]) == 64
        assert "WUWA_FMODEL_PATH" in provenance["tools"]
        assert variant.metadata_json["game_source"] == provenance


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_sibling', [False, True])
async def test_named_siblings_publish_separate_references_and_count_one_language_target(tmp_path, monkeypatch, missing_sibling):
    root = client(tmp_path)
    value = request().model_copy(update={'kinds': ['voice']})
    target = {'owner': 1, 'release_id': 2, 'path': '/Game/Voice.Voice', 'kind': 'voice', 'language': 'en'}
    payload = {'request': value.model_dump(), 'run_id': 7, 'targets': [target]}
    run = SimpleNamespace(id=7, status='queued', metadata_json={'payload': payload})
    session = AsyncMock()
    session.get.return_value = run
    session.scalar.return_value = None
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(entity_media, 'SessionFactory', factory)
    monkeypatch.setattr(entity_media, 'validate_plan', lambda _: None)
    monkeypatch.setattr(entity_media, 'missing_media_targets', AsyncMock(return_value=[target]))
    monkeypatch.setattr(entity_media, 'publish_inventory', AsyncMock(return_value={}))
    monkeypatch.setattr(entity_media, 'S3Storage', lambda _: AsyncMock())
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=11)
    service.register_variant.side_effect = lambda *args: SimpleNamespace(metadata_json={})
    references = []

    async def reference(*args, **kwargs):
        result = SimpleNamespace(**kwargs)
        references.append(result)
        return result
    service.register_reference.side_effect = reference
    monkeypatch.setattr(entity_media, 'FileRegistrationService', lambda _: service)
    executable = tmp_path / 'tool'
    executable.write_bytes(b'pinned tool')
    monkeypatch.setattr(entity_media, 'tool_path', lambda _: executable)
    raw = root / 'Client/Content/Aki/Init.bnk'
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b'pinned bank')
    files = []
    names = ['en:hirc1:' + 'a'*24, 'en:hirc1:' + 'b'*24]
    for name in names[:1] if missing_sibling else names:
        path = root / (name.rsplit(':', 1)[-1] + '.wav')
        path.write_bytes(b'decoded media')
        files.append(({**target, 'source_name': name}, path, 'audio_wav', 'audio/wav', [raw]))

    async def build(*args, provenance, voice_diagnostics, **kwargs):
        for i, (resolved, *_) in enumerate(files):
            provenance[(target['path'], resolved['source_name'])] = {'media_id': i+1,
                'voice_variant': {'expected_source_names': names, 'variant': {'source_name': resolved['source_name']}}}
        if missing_sibling:
            voice_diagnostics.append({'source': target['path'], 'language': 'en', 'reason': 'Missing exact authored voice media'})
        return files, [target['path']] if missing_sibling else []
    monkeypatch.setattr(entity_media, 'build_entity_files', build)
    await entity_media._process_entity_workspace(payload, None, root)
    assert {r.source_name for r in references} == set(names[:1] if missing_sibling else names)
    assert [r.metadata_json['game_source']['media_id'] for r in references] == list(range(1, len(files)+1))
    assert all(r.owner_node_id == 1 and r.release_id == 2 for r in references)
    assert run.status == ('partial' if missing_sibling else 'completed')
    report = run.raw_output['media_report']
    assert report['requested'] == 1 and report['found'] == int(not missing_sibling)
    assert report['missing'] == int(missing_sibling) and len(report['entries'][0]['matches']) == len(files)
    assert report['entries'][0]['status'] == ('partial' if missing_sibling else 'found')
    assert run.raw_output['published'] == len(files)


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [("en",), ("en", "ja"), ("ja",), ("en", "ja", "ko", "zh")])
async def test_partial_voice_publication_and_ambiguous_retry_stay_visible(tmp_path, monkeypatch, ambiguous):
    # Retained events have two variants in some languages and singleton media in others.
    # Exercise the real mapping/decoding/reporting pipeline, mocking external IO only.
    root = client(tmp_path)
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "plan.json").write_text(json.dumps({"id": "b" * 64, "version": "3.7.0"}))
    (voices / "status.json").write_text(json.dumps({"id": "b" * 64, "state": "downloaded"}))
    monkeypatch.setenv("WUWA_VOICE_ROOT", str(voices))
    value = request().model_copy(update={"kinds": ["voice"]})
    source = "/Game/Aki/WwiseAudio/Events/play_favor_word_test.play_favor_word_test"
    languages = ("en", "ja", "ko", "zh")
    targets = [{"owner": 1, "release_id": 2, "kind": "voice", "path": source,
                "language": language} for language in languages]
    entries, media = [], {}
    for identity, language in enumerate(languages, 1):
        for suffix, number in ((("M", identity * 10), ("F", identity * 10 + 1))
                               if language in ambiguous else (("", identity),)):
            entries.append({"DebugName": f"{language}_vo_character_line{suffix}.wav",
                            "MediaId": number, "MediaPathName": f"Media/{number}.wem"})
            if language not in ambiguous:
                wem = root / f"{number}.wem"
                wem.write_bytes(b"confirmed media")
                media[number] = (wem, value.download_id, f"Media/{number}.wem")
    event = root / "event.json"
    event.write_text(json.dumps([{"EventCookedData": {"EventLanguageMap": [{"Value": {"Media": entries}}]}}]))
    monkeypatch.setattr(entity_media, "prefetch_events", AsyncMock(return_value={source: (event, [])}))
    monkeypatch.setattr(entity_media, "prefetch_banks", AsyncMock(return_value={}))
    prefetch = AsyncMock(side_effect=lambda root, voices, identities, *args:
                         {identity: media[identity] for identity in identities})
    monkeypatch.setattr(entity_media, "prefetch_media", prefetch)
    executable = tmp_path / "tool"
    executable.write_bytes(b"pinned tool version")
    monkeypatch.setattr(entity_media, "tool_path", lambda _: executable)

    async def decode(args, log, timeout):
        with wave.open(str(args[3]), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 80)

    decoder = AsyncMock(side_effect=decode)
    monkeypatch.setattr(entity_media, "tool", decoder)
    session = AsyncMock()
    session.scalar.return_value = None
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(entity_media, "SessionFactory", factory)
    monkeypatch.setattr(entity_media, "validate_plan", lambda _: None)
    missing_targets = AsyncMock(return_value=targets)
    monkeypatch.setattr(entity_media, "missing_media_targets", missing_targets)
    monkeypatch.setattr(entity_media, "publish_inventory", AsyncMock(return_value={}))
    monkeypatch.setattr(entity_media, "S3Storage", lambda _: AsyncMock())
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=11)
    service.register_variant.side_effect = lambda *args: SimpleNamespace(metadata_json={})
    service.register_reference.side_effect = lambda *args, **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setattr(entity_media, "FileRegistrationService", lambda _: service)

    for attempt in (1, 2):
        payload = {"request": value.model_dump(), "run_id": attempt, "targets": targets}
        run = SimpleNamespace(id=attempt, status="queued", metadata_json={"payload": payload})
        session.get.return_value = run
        await entity_media._process_entity_workspace(payload, None, root)
        assert run.status == "partial" and run.error is None
        assert run.raw_output["missing_voices"] == [source]
        report = run.raw_output["media_report"]
        assert report["missing"] == len(ambiguous)
        assert report["found"] == 4 - len(ambiguous)
        by_language = {entry["language"]: entry for entry in report["entries"]}
        for language, entry in by_language.items():
            if language in ambiguous:
                assert entry["status"] == "not_resolved" and not entry["matches"]
                assert "authored selection or sequencing" in entry["reason"]
            else:
                assert entry["status"] == ("found" if attempt == 1 else "already_published")
                assert "reason" not in entry
        assert run.raw_output["published"] == (4 - len(ambiguous) if attempt == 1 else 0)
        assert run.raw_output["skipped"] == (0 if attempt == 1 else 4 - len(ambiguous))
        missing_targets.return_value = [target for target in targets if target["language"] in ambiguous]
    assert {call.kwargs["source_name"] for call in service.register_reference.await_args_list} == set(languages) - set(ambiguous)
    assert prefetch.await_args_list[0].args[2] == set(media)
    assert prefetch.await_args_list[1].args[2] == set()
    assert decoder.await_count == 4 - len(ambiguous)
    assert service.register_reference.await_count == 4 - len(ambiguous)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["variant", "reference", "commit"])
async def test_complete_target_is_committed_before_later_target_failure(tmp_path, monkeypatch, failure_stage):
    root = client(tmp_path)
    targets = [{"owner": number, "release_id": 2, "path": f"/Game/Icon{number}.Icon{number}",
                "kind": "image", "language": None} for number in (1, 2)]
    payload = {"schema_version": 1, "job_type": "media.entities",
               "request": request().model_dump(), "run_id": 7, "targets": targets}
    run = SimpleNamespace(id=7, status="queued", metadata_json={"payload": payload})
    session = AsyncMock()
    session.get.return_value = run
    session.scalar.return_value = None
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(entity_media, "SessionFactory", factory)
    monkeypatch.setattr(entity_media, "validate_plan", lambda _: None)
    monkeypatch.setattr(entity_media, "missing_media_targets", AsyncMock(return_value=targets))
    monkeypatch.setattr(entity_media, "publish_inventory", AsyncMock(return_value={}))
    monkeypatch.setattr(entity_media, "S3Storage", lambda _: AsyncMock())
    executable = tmp_path / "tool"
    executable.write_bytes(b"pinned version")
    monkeypatch.setattr(entity_media, "tool_path", lambda _: executable)
    files = []
    for number, target in enumerate(targets, 1):
        image = root / f"{number}.png"
        image.write_bytes(f"decoded-{number}".encode())
        raw = root / "exports/files/Client/Content" / f"Icon{number}.uasset"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(f"raw-{number}".encode())
        files.append((target, image, "image", "image/png", [raw]))
    monkeypatch.setattr(entity_media, "build_entity_files", AsyncMock(return_value=(files, [])))
    service = AsyncMock()
    monkeypatch.setattr(entity_media, "FileRegistrationService", lambda _: service)
    events, pending_files, pending_variants, pending_refs = [], [], [], []
    committed_files, committed_variants, committed_refs = [], [], []
    committed_run = copy.deepcopy(vars(run))
    owner = None

    async def execute(statement, parameters):
        nonlocal owner
        owner = parameters["id"]
        events.append(("lock", owner))

    async def register_file(db, path, *args, **kwargs):
        events.append(("file", owner))
        pending_files.append(path.name)
        return SimpleNamespace(id=len(committed_files) + len(pending_files))

    async def register_variant(*args):
        if owner == 2 and failure_stage == "variant":
            raise OSError("second target failed")
        variant = SimpleNamespace(metadata_json={})
        pending_variants.append(variant)
        return variant

    async def register_reference(db, **kwargs):
        if owner == 2 and failure_stage == "reference":
            raise OSError("second target failed")
        ref = SimpleNamespace(**kwargs)
        pending_refs.append(ref)
        return ref

    async def commit():
        nonlocal committed_run, owner
        if pending_refs and owner == 2 and failure_stage == "commit":
            raise OSError("second target failed")
        for ref in pending_refs:
            assert ref.metadata_json["game_source"]["source_files"]
        assert all(v.metadata_json["game_source"] for v in pending_variants)
        committed_files.extend(pending_files)
        committed_variants.extend(copy.deepcopy(pending_variants))
        committed_refs.extend(copy.deepcopy(pending_refs))
        events.append(("commit", len(committed_refs)))
        committed_run = copy.deepcopy(vars(run))
        pending_files.clear()
        pending_variants.clear()
        pending_refs.clear()
        owner = None  # Transaction-scoped owner locks are released by commit.

    async def rollback():
        nonlocal owner
        pending_files.clear()
        pending_variants.clear()
        pending_refs.clear()
        vars(run).clear()
        vars(run).update(copy.deepcopy(committed_run))
        owner = None
        events.append(("rollback", len(committed_refs)))

    session.execute.side_effect = execute
    session.commit.side_effect = commit
    session.rollback.side_effect = rollback
    service.register_file.side_effect = register_file
    service.register_variant.side_effect = register_variant
    service.register_reference.side_effect = register_reference

    @asynccontextmanager
    async def connect():
        yield AsyncMock()

    @asynccontextmanager
    async def unlocked(*args):
        yield

    async def workspace(payload, connection):
        await entity_media._process_entity_workspace(payload, connection, root)

    monkeypatch.setattr(entity_media, "engine", SimpleNamespace(connect=connect))
    monkeypatch.setattr(entity_media, "job_lock", unlocked)
    monkeypatch.setattr(entity_media, "_process_entity_media", workspace)
    with pytest.raises(OSError, match="second target"):
        await entity_media._locked_process_entity_media(payload)
    assert len(committed_refs) == 1 and committed_refs[0].source_path == targets[0]["path"]
    assert committed_files == ["1.png", "Icon1.uasset"] and len(committed_variants) == 1
    assert events.index(("commit", 1)) < events.index(("lock", 2))
    assert ("rollback", 1) in events
    assert run.status == "failed" and run.raw_output["published"] == 1
