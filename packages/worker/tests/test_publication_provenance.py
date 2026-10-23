from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story_worker import cutscene_import, voice_import
from wuwa_story_worker.publication_provenance import (
    pinned_source,
    publication_source,
    verified_references,
)


def context(**changes):
    return {"asset_version": "3.7.0", "download_id": "a" * 64, "tier": "hd",
            "voice_plan_id": "b" * 64, "tools": {"WUWA_VGMSTREAM_PATH": "c" * 64}, **changes}


def reference(**changes):
    metadata = {"game_source": {**context(), "schema_version": 1,
                               "package_path": "Client/Content/Audio/en_vo_test.wem",
                               "source_files": [{"package_path": "Client/Content/Audio/en_vo_test.wem",
                                                 "sha256": "d" * 64}]},
                "publication_file_ids": [10, 11]}
    return SimpleNamespace(file_id=11, source_path="Client/Content/Audio/en_vo_test.wem",
                           source_name="en_vo_test.wem", owner_node_id=1,
                           metadata_json=metadata, **changes)


def factory(monkeypatch, module, session):
    value = Mock()
    value.return_value.__aenter__ = AsyncMock(return_value=session)
    value.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module, "SessionFactory", value)


@pytest.mark.asyncio
@pytest.mark.parametrize("original_size,playable_size,expected", [
    (12, 12, True), (None, 12, False), (12, None, False), (12, 9, False),
])
async def test_every_blob_is_verified_even_when_database_flag_is_stale(original_size, playable_size, expected):
    ref = reference()
    locations = [SimpleNamespace(object_key=name, available=False) for name in ("original", "playable")]
    session = AsyncMock()
    session.scalars.return_value = [ref]
    session.execute.return_value = [(10, 12, locations[0]), (11, 12, locations[1])]
    sizes = {"original": original_size, "playable": playable_size}
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(
        side_effect=lambda key: None if sizes[key] is None else SimpleNamespace(size_bytes=sizes[key])))
    assert bool(await verified_references(session, storage, "voice_audio", context())) is expected
    assert storage.stat.await_count == 2
    assert locations[0].available is (original_size == 12)
    assert locations[1].available is (playable_size == 12)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"tools": {"WUWA_VGMSTREAM_PATH": "new"}},
                                         {"download_id": "new"}, {"voice_plan_id": "new"},
                                         {"source_files": []}, {"schema_version": 0}])
async def test_legacy_and_incompatible_publications_are_retryable(change):
    ref = reference()
    ref.metadata_json["game_source"].update(change)
    session = AsyncMock()
    session.scalars.return_value = [ref]
    storage = SimpleNamespace(stat=AsyncMock())
    assert await verified_references(session, storage, "voice_audio", context()) == []
    storage.stat.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_failure_propagates_instead_of_treating_outage_as_missing():
    session = AsyncMock()
    session.scalars.return_value = [reference(), reference()]
    session.execute.return_value = [(10, 12, SimpleNamespace(object_key="shared"))]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(side_effect=OSError("offline")))
    with pytest.raises(OSError, match="offline"):
        await verified_references(session, storage, "voice_audio", context())
    storage.stat.assert_awaited_once_with("shared")


@pytest.mark.asyncio
@pytest.mark.parametrize("current", [[], [(20, 99)], [(20, 12)]])
async def test_deleted_or_replaced_segment_reference_prevents_complete_reuse(current):
    ref = reference()
    ref.metadata_json["publication_references"] = [{"id": 20, "file_id": 12}]
    session = AsyncMock()
    session.scalars.return_value = [ref]
    session.execute.side_effect = [current, [(10, 12, SimpleNamespace(object_key="original")),
                                            (11, 12, SimpleNamespace(object_key="playable"))]]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    result = await verified_references(session, storage, "cutscene_flow", context())
    assert bool(result) is (current == [(20, 12)])


def test_provenance_retains_archive_map_hashes_and_excludes_ephemeral_identity(tmp_path):
    path = tmp_path / "Client/Content/Audio/en_vo_test.wem"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"source")
    value = context(workspace_id=42, release_id=9, archive_keys=["Audio/Windows/base.pak"],
                    path_map={path.name: "Client/Content/Audio/en_vo_test.wem"})
    result = publication_source(value, {"path": "Client/Content/Audio/en_vo_test.wem", "kind": "voice"}, [path])
    assert result["archive_keys"] == value["archive_keys"]
    assert result["path_map"] == value["path_map"]
    assert result["source_files"][0]["package_path"] == "Client/Content/Audio/en_vo_test.wem"
    assert len(result["source_files"][0]["sha256"]) == 64
    assert "workspace_id" not in result and "release_id" not in result
    with pytest.raises(ValueError, match="asset version"):
        pinned_source(value, "3.6.0")


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_owner,missing_track", [(False, False), (True, False), (False, True)])
async def test_global_voice_query_requires_every_owner_and_inventory_track(monkeypatch, missing_owner, missing_track):
    session = AsyncMock()
    session.scalars.return_value = [SimpleNamespace(node_id=i, file_name="vo_test") for i in (1, 2)]
    factory(monkeypatch, voice_import, session)
    inventory = [{"source_name": f"{lang}_vo_test.wem", "path": f"Audio/{lang}.wem", "language": lang}
                 for lang in ("en", "ja", "ko", "zh")]
    refs = [SimpleNamespace(owner_node_id=owner, source_name=track["source_name"], source_path=track["path"],
                            metadata_json={"voice_name": "vo_test", "voice_tracks": inventory,
                                           "voice_inventory_complete": True})
            for owner in (1, 2) for track in inventory
            if not (missing_owner and owner == 2) and not (missing_track and track["language"] == "zh")]
    monkeypatch.setattr(voice_import, "verified_references", AsyncMock(return_value=refs))
    result = await voice_import.missing_global_voice_identities({"vo_test"}, source_context=context(), storage=Mock())
    assert result == ({"vo_test"} if missing_owner or missing_track else set())


@pytest.mark.asyncio
async def test_shared_source_is_decoded_and_uploaded_once_for_multiple_owners(tmp_path, monkeypatch):
    import wave

    path = tmp_path / "Client/Content/Audio/en_vo_test.wem"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"source")
    session = AsyncMock()
    session.scalars.return_value = [SimpleNamespace(node_id=i, file_name="vo_test") for i in (1, 2)]
    session.scalar.return_value = None
    factory(monkeypatch, voice_import, session)
    monkeypatch.setattr(voice_import, "get_settings", lambda: None)
    monkeypatch.setattr(voice_import, "S3Storage", lambda _: AsyncMock())
    monkeypatch.setattr(voice_import, "verified_references", AsyncMock(return_value=[]))
    service = AsyncMock()
    service.register_file.side_effect = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
    refs = [SimpleNamespace(), SimpleNamespace()]
    service.register_reference.side_effect = refs
    monkeypatch.setattr(voice_import, "FileRegistrationService", lambda _: service)

    def decode(args, **kwargs):
        with wave.open(args[3], "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(48000)
            stream.writeframes(b"\0\0" * 480)

    decoder = Mock(side_effect=decode)
    monkeypatch.setattr(voice_import.subprocess, "run", decoder)
    result = await voice_import.import_voice_sample(
        tmp_path, tmp_path / "decoder", "3.7.0", require_all_languages=False,
        grouped={"vo_test": {("en", None): path}}, source_context=context())
    assert result["tracks"] == 2
    decoder.assert_called_once()
    assert service.register_file.await_count == 2
    assert refs[0].file_id == refs[1].file_id == 11
    assert refs[0].metadata_json["publication_file_ids"] == [10, 11]
    assert not refs[0].metadata_json["voice_inventory_complete"]
    assert refs[0].source_path == "Client/Content/Audio/en_vo_test.wem"
    assert service.register_variant.return_value.metadata_json["game_source"]["source_files"]


@pytest.mark.asyncio
async def test_verified_voice_blob_is_attached_to_new_owner_without_decoding(tmp_path, monkeypatch):
    path = tmp_path / "Client/Content/Audio/en_vo_test.wem"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"source")
    source = publication_source(context(), {"path": "Client/Content/Audio/en_vo_test.wem", "kind": "voice"}, [path])
    existing = reference()
    existing.metadata_json.update(game_source=source, original_file_id=10)
    session = AsyncMock()
    session.scalars.return_value = [SimpleNamespace(node_id=2, file_name="vo_test")]
    session.scalar.return_value = None
    factory(monkeypatch, voice_import, session)
    monkeypatch.setattr(voice_import, "get_settings", lambda: None)
    monkeypatch.setattr(voice_import, "S3Storage", lambda _: AsyncMock())
    monkeypatch.setattr(voice_import, "verified_references", AsyncMock(return_value=[existing]))
    service = AsyncMock()
    ref = SimpleNamespace()
    service.register_reference.return_value = ref
    monkeypatch.setattr(voice_import, "FileRegistrationService", lambda _: service)
    decoder = Mock(side_effect=AssertionError("Compatible publication must not decode again"))
    monkeypatch.setattr(voice_import.subprocess, "run", decoder)
    await voice_import.import_voice_sample(
        tmp_path, tmp_path / "decoder", "3.7.0", require_all_languages=False,
        grouped={"vo_test": {("en", None): path}}, source_context=context())
    assert ref.file_id == 11 and ref.metadata_json["original_file_id"] == 10
    assert service.register_reference.await_args.kwargs["owner_node_id"] == 2
    service.register_file.assert_not_awaited()
    decoder.assert_not_called()


def test_cutscene_closure_covers_stems_evidence_and_silent_segments():
    ref = SimpleNamespace(file_id=1, metadata_json={
        "original_file_id": 2, "analysis_file_id": 3, "source_file_ids": [4],
        "tracks": [{"file_id": 5, "source_file_ids": [6, 7]}],
        "videos": {"full": 8, "branch": 9}})
    assert cutscene_import.publication_file_ids(ref) == set(range(1, 10))


@pytest.mark.parametrize("missing", [None, "stem", "silent_segment", "bundle"])
def test_completion_requires_every_requested_stem_and_silent_segment(missing):
    bundle = SimpleNamespace(metadata_json={
        "tracks": [{"role": "voice", "language": language, "file_id": index + 1}
                   for index, language in enumerate(("en", "ja"))],
        "videos": {"full": 3, "branch": 4}})
    if missing == "stem":
        bundle.metadata_json["tracks"].pop()
    if missing == "silent_segment":
        bundle.metadata_json["videos"].pop("branch")
    if missing == "bundle":
        bundle = None
    assert cutscene_import.audio_bundle_complete(bundle, {"en", "ja"}, {"branch"}) is (missing is None)


@pytest.mark.asyncio
@pytest.mark.parametrize("complete,hash_matches", [(True, True), (False, True), (True, False)])
async def test_cutscene_query_rejects_partial_or_other_recipe(monkeypatch, complete, hash_matches):
    session = AsyncMock()
    factory(monkeypatch, cutscene_import, session)
    ref = SimpleNamespace(source_path="cutscene:test", metadata_json={
        "publication_complete": complete, "game_source": {"recipe_sha256": "expected" if hash_matches else "old"}})
    monkeypatch.setattr(cutscene_import, "verified_references", AsyncMock(return_value=[ref]))
    result = await cutscene_import.missing_cutscene_identities(
        {"cutscene:test"}, source_context=context(), storage=Mock(), recipe_hashes={"cutscene:test": "expected"})
    assert result == (set() if complete and hash_matches else {"cutscene:test"})


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_blob", [None, 31, 32])
async def test_completion_records_and_verifies_entire_publication(tmp_path, monkeypatch, missing_blob):
    from wuwa_story.ingestion.cutscenes import CutsceneRecipe

    spec = CutsceneRecipe.model_validate({"cutscene": "cutscene:test", "asset_version": "3.7.0",
        "videos": [{"asset": "asset:ue:/Game/Video.Video", "soundtrack": [{"bank": "en.bnk", "language": "en"}]}],
        "flow": {"entry": "main", "nodes": [{"id": "main", "kind": "clip", "asset": "asset:ue:/Game/Video.Video"}],
                 "evidence": "authored"}})
    nodes = [SimpleNamespace(id=1, canonical_key="cutscene:test"),
             SimpleNamespace(id=2, canonical_key="asset:ue:/Game/Video.Video")]
    flow = SimpleNamespace(id=3, file_id=20, owner_node_id=1, source_path="cutscene:test",
                           reference_type="cutscene_flow", metadata_json={"asset_version": "3.7.0"})
    video = SimpleNamespace(id=1, file_id=11, owner_node_id=2, reference_type="cutscene_video",
                            metadata_json={"original_file_id": 10, "source_file_ids": [12]})
    bundle = SimpleNamespace(id=2, file_id=30, owner_node_id=2, reference_type="cutscene_audio_bundle",
                             metadata_json={"tracks": [{"role": "voice", "language": "en", "file_id": 31,
                                                        "source_file_ids": [12]}], "videos": {"full": 32}})
    variant = SimpleNamespace(variant_type="cutscene_silent_video", metadata_json={})
    session = AsyncMock()
    session.scalars.side_effect = [nodes, [flow, video, bundle], [variant], [flow]]
    ids = {10, 11, 12, 20, 30, 31, 32}
    session.execute.side_effect = [None, None, [(1, 11), (2, 30), (3, 20)],
        [(identity, 12, SimpleNamespace(object_key=str(identity), available=False)) for identity in ids]]
    factory(monkeypatch, cutscene_import, session)
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(
        side_effect=lambda key: None if int(key) == missing_blob else SimpleNamespace(size_bytes=12)))
    provenance = reference().metadata_json["game_source"]
    await cutscene_import.complete_cutscene_publication(
        spec, {"asset:ue:/Game/Video.Video": provenance}, storage, complete=True,
        published_references={1: 11, 3: 20})
    assert flow.metadata_json["publication_complete"] is (missing_blob is None)
    assert set(flow.metadata_json["publication_file_ids"]) == ids
    assert {item["id"] for item in flow.metadata_json["publication_references"]} == {1, 2, 3}
    assert flow.metadata_json["game_source"]["package_path"] == "cutscene:test"
    assert variant.metadata_json["game_source"]["source_files"]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_verified_cutscene_skips_before_reading_exports_or_running_tools(tmp_path, monkeypatch):
    import json

    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"cutscene": "cutscene:test", "asset_version": "3.7.0",
        "videos": [{"asset": "asset:ue:/Game/Video.Video"}],
        "flow": {"entry": "main", "nodes": [{"id": "main", "kind": "clip", "asset": "asset:ue:/Game/Video.Video"}],
                 "evidence": "authored"}}))
    monkeypatch.setattr(cutscene_import, "missing_cutscene_identities", AsyncMock(return_value=set()))
    tools = AsyncMock()
    monkeypatch.setattr(cutscene_import, "run_tool", tools)
    absent = tmp_path / "absent"
    result = await cutscene_import.import_cutscene_recipe(
        recipe, absent, absent, absent, absent, absent, absent, source_context=context())
    assert result["skipped"] is True
    assert not absent.exists()
    tools.assert_not_awaited()


@pytest.mark.asyncio
async def test_cutscene_persists_original_sources_and_stable_unreal_identity(tmp_path, monkeypatch):
    import json

    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"cutscene": "cutscene:test", "asset_version": "3.7.0",
        "videos": [{"asset": "asset:ue:/Game/Video.Video"}],
        "flow": {"entry": "main", "nodes": [{"id": "main", "kind": "clip", "asset": "asset:ue:/Game/Video.Video"}],
                 "evidence": "authored"}}))
    wrapper = tmp_path / "Client/Content/Video.uexp"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(b"filepath://./Aki/Movies/Video.mp4\0")
    movie = tmp_path / "Client/Content/Aki/Movies/Video.mp4"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"game movie")
    nodes = [SimpleNamespace(id=1, canonical_key="cutscene:test"),
             SimpleNamespace(id=2, canonical_key="asset:ue:/Game/Video.Video")]
    session = AsyncMock()
    session.scalars.side_effect = [nodes, [3], [2]]
    session.scalar.return_value = None
    factory(monkeypatch, cutscene_import, session)
    monkeypatch.setattr(cutscene_import, "missing_cutscene_identities", AsyncMock(return_value={"cutscene:test"}))
    monkeypatch.setattr(cutscene_import, "get_settings", lambda: None)
    monkeypatch.setattr(cutscene_import, "S3Storage", lambda _: AsyncMock())
    monkeypatch.setattr(cutscene_import, "video_duration", lambda *args: (1.0, False))

    async def convert(args):
        from pathlib import Path
        Path(args[-1]).write_bytes(b"playable")

    monkeypatch.setattr(cutscene_import, "run_tool", convert)
    service = AsyncMock()
    files, refs = [], []

    async def register_file(session, path, kind, **kwargs):
        files.append((path.name, kind))
        return SimpleNamespace(id=len(files))

    async def register_ref(session, **kwargs):
        ref = SimpleNamespace(id=len(refs) + 1, **kwargs)
        refs.append(ref)
        return ref

    service.register_file.side_effect = register_file
    service.register_reference.side_effect = register_ref
    monkeypatch.setattr(cutscene_import, "FileRegistrationService", lambda _: service)
    complete = AsyncMock()
    monkeypatch.setattr(cutscene_import, "complete_cutscene_publication", complete)
    result = await cutscene_import.import_cutscene_recipe(
        recipe, tmp_path, tmp_path, tmp_path, tmp_path / "work", tmp_path / "decoder", tmp_path / "ffmpeg",
        source_context=context())
    assert result["videos"] == 1
    video = refs[0]
    assert video.source_path == "/Game/Video.Video"
    assert video.metadata_json["original_file_id"] == 1
    assert video.metadata_json["source_file_ids"] == [3]
    provenance = video.metadata_json["game_source"]
    assert {file["package_path"] for file in provenance["source_files"]} == {
        "Client/Content/Video.uexp", "Client/Content/Aki/Movies/Video.mp4"}
    assert provenance["recipe"]["cutscene"] == "cutscene:test"
    assert len(provenance["recipe_sha256"]) == 64
    assert service.register_variant.return_value.metadata_json["game_source"] == provenance
    assert complete.await_args.kwargs["complete"] is True
