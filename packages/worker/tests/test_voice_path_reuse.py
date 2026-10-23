import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story_worker import voice_import


def context(**changes):
    return {"asset_version": "3.7.0", "download_id": "a" * 64, "tier": "hd",
            "voice_plan_id": "b" * 64, "tools": {"WUWA_VGMSTREAM_PATH": "c" * 64}, **changes}


def paths(suffixes=("",)):
    return {f"Audio/{lang}_vo_test{suffix}.wem" for lang in ("en", "ja", "ko", "zh") for suffix in suffixes}


def published(selected):
    return [SimpleNamespace(owner_node_id=1, source_path=track["path"], source_name=track["source_name"],
                            file_id=index + 100, metadata_json={
                                "voice_name": "vo_test", "asset_version": "3.7.0", "language": track["language"],
                                "rover": track["rover"], "original_file_id": index + 200,
                                "publication_file_ids": [index + 100, index + 200]})
            for index, track in enumerate(voice_import.voice_path_roster({"vo_test"}, selected)["vo_test"])]


def setup(monkeypatch, refs, owners=(1,)):
    session = AsyncMock()
    session.scalars.side_effect = [[SimpleNamespace(node_id=owner, file_name="vo_test") for owner in owners], refs]
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(voice_import, "SessionFactory", factory)
    verified = AsyncMock(return_value=refs)
    monkeypatch.setattr(voice_import, "verified_references", verified)
    service = AsyncMock()
    service.register_reference.side_effect = lambda session, **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setattr(voice_import, "FileRegistrationService", lambda _: service)
    return session, service, verified


@pytest.mark.asyncio
async def test_only_missing_language_is_selected_from_full_archive_roster(monkeypatch):
    roster = paths()
    refs = published(roster - {"Audio/ja_vo_test.wem"})
    session, service, verified = setup(monkeypatch, refs)
    missing = await voice_import.missing_global_voice_paths({"vo_test"}, roster, source_context=context(), storage=Mock())
    assert missing == {"Audio/ja_vo_test.wem"}
    assert verified.await_args.args[4] == roster
    assert all(len(ref.metadata_json["voice_tracks"]) == 4 for ref in refs)
    assert all(not ref.metadata_json["voice_inventory_complete"] for ref in refs)
    service.register_reference.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_all_languages_of_female_do_not_hide_missing_authored_male(monkeypatch):
    refs = published(paths(("_F",)))
    setup(monkeypatch, refs)
    missing = await voice_import.missing_global_voice_paths(
        {"vo_test"}, paths(("_F", "_M")), source_context=context(), storage=Mock())
    assert missing == paths(("_M",))
    assert all(len(ref.metadata_json["voice_tracks"]) == 8 for ref in refs)
    assert all(ref.metadata_json["voice_inventory_authoritative"] for ref in refs)
    assert not any(ref.metadata_json["voice_inventory_complete"] for ref in refs)


@pytest.mark.asyncio
async def test_new_owner_is_attached_to_verified_variants_without_extracting(monkeypatch):
    roster = paths(("_F", "_M"))
    refs = published(roster)
    session, service, verified = setup(monkeypatch, refs, owners=(1, 2))
    assert await voice_import.missing_global_voice_paths(
        {"vo_test"}, roster, source_context=context(), storage=Mock()) == set()
    assert service.register_reference.await_count == 8
    assert {call.kwargs["owner_node_id"] for call in service.register_reference.await_args_list} == {2}
    service.register_file.assert_not_awaited()
    verified.assert_awaited_once()
    assert session.execute.await_count == 2  # One lock per owner, rather than per track.
    assert all(ref.metadata_json["voice_inventory_complete"] for ref in refs)


@pytest.mark.asyncio
async def test_coarse_query_cannot_trust_an_export_only_variant_inventory(monkeypatch):
    refs = published(paths(("_F",)))
    for ref in refs:
        ref.metadata_json.update(voice_tracks=voice_import.voice_path_roster({"vo_test"}, paths(("_F",)))["vo_test"],
                                 voice_inventory_complete=True)
    setup(monkeypatch, refs)
    assert await voice_import.missing_global_voice_identities(
        {"vo_test"}, source_context=context(), storage=Mock()) == {"vo_test"}


@pytest.mark.asyncio
async def test_supplied_roster_overrides_previously_published_variant_inventory(monkeypatch):
    refs = published(paths(("_F",)))
    for ref in refs:
        ref.metadata_json.update(voice_tracks=voice_import.voice_path_roster({"vo_test"}, paths(("_F",)))["vo_test"],
                                 voice_inventory_complete=True, voice_inventory_authoritative=True)
    setup(monkeypatch, refs)
    assert await voice_import.missing_global_voice_identities(
        {"vo_test"}, source_context=context(voice_paths=paths(("_F", "_M"))), storage=Mock()) == {"vo_test"}


def test_literal_f_identity_is_not_treated_as_a_rover_variant():
    roster = voice_import.voice_path_roster({"vo_test_F"}, paths(("_F",)))
    assert {track["rover"] for track in roster["vo_test_F"]} == {None}


@pytest.mark.parametrize("roster", [["../en_vo_test.wem"], ["/Audio/en_vo_test.wem"],
                                    ["Audio/en_vo_test.wem", "Other/en_vo_test.wem"]])
def test_unsafe_or_ambiguous_archive_rosters_fail_closed(roster):
    with pytest.raises(ValueError):
        voice_import.voice_path_roster({"vo_test"}, roster)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_male", [False, True])
async def test_importing_one_missing_track_preserves_verified_other_languages(tmp_path, monkeypatch, missing_male):
    roster = paths(("_F", "_M")) if missing_male else paths()
    repaired = "Audio/ja_vo_test_F.wem" if missing_male else "Audio/ja_vo_test.wem"
    source = tmp_path / repaired
    source.parent.mkdir(parents=True)
    source.write_bytes(b"game voice")
    refs = published((paths(("_F",)) if missing_male else roster) - {repaired})
    session, service, _ = setup(monkeypatch, refs)
    session.scalar.return_value = None
    service.register_file.side_effect = [SimpleNamespace(id=10), SimpleNamespace(id=11)]
    monkeypatch.setattr(voice_import, "get_settings", lambda: None)
    monkeypatch.setattr(voice_import, "S3Storage", lambda _: AsyncMock())

    def decode(args, **kwargs):
        with wave.open(args[3], "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(b"\0\0" * 480)

    decoder = Mock(side_effect=decode)
    monkeypatch.setattr(voice_import.subprocess, "run", decoder)
    result = await voice_import.import_voice_sample(
        tmp_path, Path("decoder"), "3.7.0", require_all_languages=False,
        grouped={"vo_test": {("ja", "female" if missing_male else None): source}}, names={"vo_test"},
        source_context=context(voice_paths=sorted(roster), path_map={repaired: repaired}))
    assert result["missing_voices"] == (sorted(paths(("_M",))) if missing_male else [])
    decoder.assert_called_once()
    assert all(ref.metadata_json["voice_inventory_complete"] is (not missing_male) for ref in refs)
    assert all({track["path"] for track in ref.metadata_json["voice_tracks"]} == roster for ref in refs)
    assert service.register_file.await_count == 2
