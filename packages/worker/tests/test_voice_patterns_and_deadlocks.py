import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import DBAPIError

from wuwa_story_worker import archive_index, broker, notifications, release_media, voice_import


def test_selected_voice_paths_and_roster_resolve_varparams_templates(tmp_path):
    log = tmp_path / "archive-index.log"
    archive_files = [
        f"Client/Content/Aki/WwiseAudio_Generated/WwiseExternalSource/{lang}_vo_MAIN_RYAM_69_1_team_MAIN_RYAM_16_{idx}.wem"
        for lang in ("en", "ja", "ko", "zh")
        for idx in (7, 8, 9)
    ] + [
        "Client/Content/Aki/WwiseAudio_Generated/WwiseExternalSource/en_vo_UNRELATED.wem",
    ]
    log.write_text(
        "".join(f"[File] {path}\n" for path in archive_files)
        + f"[Done] Listed {len(archive_files)} files\n",
        encoding="utf-8",
    )
    index = archive_index.index_voice_paths(log)
    template = "vo_MAIN_RYAM_69_1_team_{VarParams}"

    selected = release_media.selected_voice_paths(index, {template})
    assert len(selected) == 12
    assert all("UNRELATED" not in path for path in selected)

    roster = voice_import.voice_path_roster({template}, selected)
    assert len(roster[template]) == 12
    assert {track["variant"] for track in roster[template]} == {
        "MAIN_RYAM_16_7",
        "MAIN_RYAM_16_8",
        "MAIN_RYAM_16_9",
    }

    extracted = tmp_path / "extracted"
    for rel in selected:
        target = extracted / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"wem")
    grouped = voice_import.exported_voices(extracted, {template})
    assert len(grouped[template]) == 12
    assert ("en", None, "MAIN_RYAM_16_7") in grouped[template]


@pytest.mark.asyncio
async def test_voices_completes_cleanly_when_only_unshipped_plot_audio_absent(monkeypatch):
    monkeypatch.setattr(release_media, "requested_voice_paths", AsyncMock(return_value=[]))
    monkeypatch.setattr(release_media, "missing_global_voice_paths", AsyncMock(return_value=set()))
    monkeypatch.setattr(release_media, "cleanup_published_output", AsyncMock())
    monkeypatch.setattr(release_media, "source_context", AsyncMock(return_value={"tools": {}}))

    result, status = await release_media.voices(
        {"targets": ["vo_TZZNQ_63_14"]},
        {"workspace_id": 11, "asset_version": "3.8.0"},
    )
    assert status == "completed"
    assert result["missing_voices"] == []
    assert result["unshipped_voices"] == [
        "en_vo_TZZNQ_63_14",
        "ja_vo_TZZNQ_63_14",
        "ko_vo_TZZNQ_63_14",
        "zh_vo_TZZNQ_63_14",
    ]


def test_notifications_suppresses_telegram_alert_for_not_in_archives_only_voices():
    unshipped_run = SimpleNamespace(
        id=37382,
        status="partial",
        error=None,
        metadata_json={"request": {"kind": "voices", "game_version": "3.8.0"}},
        raw_output={
            "media_report": {
                "found": 0,
                "missing": 4,
                "entries": [
                    {"expected": f"{lang}_vo_TZZNQ_63_14.wem", "status": "not_in_archives"}
                    for lang in ("en", "ja", "ko", "zh")
                ],
            }
        },
    )
    assert notifications.alert_text(unshipped_run, "release_media") is None

    broken_export_run = SimpleNamespace(
        id=37383,
        status="partial",
        error=None,
        metadata_json={"request": {"kind": "voices", "game_version": "3.8.0"}},
        raw_output={
            "media_report": {
                "found": 3,
                "missing": 1,
                "entries": [
                    {"expected": "en_vo_TEST_1.wem", "status": "export_missing"},
                ],
            }
        },
    )
    alert = notifications.alert_text(broken_export_run, "release_media")
    assert alert is not None and "Task #37383" in alert


def test_transient_job_error_detects_asyncpg_wrapped_deadlock():
    class FakeAsyncpgDeadlock(Exception):
        sqlstate = "40P01"

    class FakeWrapper(Exception):
        pass

    wrapper = FakeWrapper("deadlock detected")
    wrapper.__cause__ = FakeAsyncpgDeadlock("deadlock detected")
    err = DBAPIError("UPDATE storage.file_variant ...", {}, wrapper)
    assert broker.transient_job_error(err) is True


@pytest.mark.asyncio
async def test_import_voice_sample_retries_deadlock_and_skips_redundant_variant_update(tmp_path, monkeypatch):
    source = tmp_path / "Audio/en_vo_test.wem"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"game voice")

    class FakeAsyncpgDeadlock(Exception):
        sqlstate = "40P01"

    deadlock = DBAPIError("UPDATE storage.file_variant ...", {}, FakeAsyncpgDeadlock("deadlock"))
    session = AsyncMock()
    session.scalars.return_value = [SimpleNamespace(node_id=1, file_name="vo_test")]
    session.scalar.return_value = None
    # First commit is after verified_references read; second raises deadlock; third succeeds.
    session.commit.side_effect = [None, deadlock, None]

    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(voice_import, "SessionFactory", factory)
    monkeypatch.setattr(voice_import, "verified_references", AsyncMock(return_value=[]))
    monkeypatch.setattr(voice_import, "get_settings", lambda: None)
    monkeypatch.setattr(voice_import, "S3Storage", lambda _: AsyncMock())
    monkeypatch.setattr(voice_import.asyncio, "sleep", AsyncMock())

    existing_variant = SimpleNamespace(metadata_json={"game_source": {"already": "set"}})
    service = AsyncMock()
    service.prepare_file.side_effect = [
        SimpleNamespace(sha256="aaa"),
        SimpleNamespace(sha256="bbb"),
    ]
    service.register_file.side_effect = [
        SimpleNamespace(id=10),
        SimpleNamespace(id=11),
        SimpleNamespace(id=10),
        SimpleNamespace(id=11),
    ]
    service.register_variant.return_value = existing_variant
    service.register_reference.side_effect = lambda _session, **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setattr(voice_import, "FileRegistrationService", lambda _: service)

    def decode(args, **_kwargs):
        with wave.open(args[3], "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(b"\0\0" * 480)

    monkeypatch.setattr(voice_import.subprocess, "run", Mock(side_effect=decode))

    result = await voice_import.import_voice_sample(
        tmp_path,
        Path("decoder"),
        "3.8.0",
        require_all_languages=False,
        grouped={"vo_test": {("en", None): source}},
        names={"vo_test"},
        source_context={
            "asset_version": "3.8.0",
            "download_id": "a" * 64,
            "tier": "hd",
            "voice_plan_id": "b" * 64,
            "tools": {"WUWA_VGMSTREAM_PATH": "c" * 64},
            "voice_paths": ["Audio/en_vo_test.wem"],
            "path_map": {"Audio/en_vo_test.wem": "Audio/en_vo_test.wem"},
        },
    )
    assert result["tracks"] == 1
    assert session.rollback.await_count == 1
    assert session.commit.await_count == 3
    # Variant already had game_source, so it was not overwritten (avoiding row lock contention).
    assert existing_variant.metadata_json == {"game_source": {"already": "set"}}
