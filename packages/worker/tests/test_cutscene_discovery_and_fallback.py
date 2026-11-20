"""Tests for non-VideoData cutscene discovery, archive fallback planning, and assembly."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import cutscene_sources, release_media
from wuwa_story_worker.cutscene_plan import plan_cutscene
from wuwa_story_worker.cutscene_sources import (
    levela_sequence_code,
    resolve_discovered_cutscene_sources,
    scan_archive_cutscene_candidates,
)


def _create_archive_index(path: Path, archive_paths: list[str]) -> Path:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE files (name TEXT, path TEXT)")
        db.executemany(
            "INSERT INTO files VALUES (?, ?)",
            [(item.rsplit("/", 1)[-1].lower(), item) for item in archive_paths],
        )
    return path


def _create_empty_videodata(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE videodata (CgId INT, GirlOrBoy INT, CgName TEXT, BinData BLOB);"
            "CREATE TABLE videosound (CaptionId INT, GirlOrBoy INT, CgName TEXT, BinData BLOB);"
        )
    return path


@pytest.mark.parametrize(
    "engine_path,expected",
    [
        ("/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/DA_M0394.DA_M0394", "M0394"),
        ("asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/DA_M0396.DA_M0396", "M0396"),
        ("/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0397/DA_M0397.DA_M0397", "M0397"),
        ("/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/DA_M0398.DA_M0398", "M0398"),
        ("/Game/Aki/Sequence/LevelA_Seq/POI/03_6/P0063/DA_.DA_", None),
        ("/Game/Aki/Sequence/LevelB_Seq/Main/Section3_8/M3_14_12/DA_M3_14_12.DA_M3_14_12", None),
    ],
)
def test_levela_sequence_code_extraction(engine_path, expected):
    assert levela_sequence_code(engine_path) == expected


def test_scan_and_resolve_levela_and_activity_archive_sources(tmp_path):
    index = _create_archive_index(
        tmp_path / "archive-index.sqlite",
        [
            # M0394: StreamMediaSource wrappers + MP4s
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nan.uasset",
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nan.uexp",
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nv.uasset",
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nv.uexp",
            "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0394_Nanzhu.mp4",
            "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0394_Nvzhu.mp4",
            # M0396: embedded Cam006_012.mp4 + unrelated Anim uasset
            "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4",
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/Anim/M0396_Cam007.uasset",
            # M0397: direct MP4s only
            "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0397_Nanzhu.mp4",
            "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0397_Nvzhu.mp4",
            # M0398: pure in-engine sequence, NO mp4
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/DA_M0398.uasset",
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/M0398_All.uasset",
            "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/Anim/M0398_Cam001.uasset",
            # Activity38 RoleTower
            "Client/Content/Aki/UI/UIResources/UiActivity/Mp4/Activity38/RoleTower/Charact_ChangLi.uasset",
            "Client/Content/Aki/Movies/UI/Activity/Activity38/RoleTower/Charact_ChangLi.mp4",
        ],
    )
    scanned = scan_archive_cutscene_candidates(
        index, ["M0394", "M0396", "M0397", "M0398", "Charact_ChangLi"]
    )
    assert "M0398" not in scanned
    assert set(scanned) == {"M0394", "M0396", "M0397", "Charact_ChangLi"}
    assert "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/Anim/M0396_Cam007.uasset" not in scanned["M0396"]["wrappers"]

    assets = tmp_path / "assets"
    for suffix, movie in (
        ("Nan", "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0394_Nanzhu.mp4"),
        ("Nv", "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0394_Nvzhu.mp4"),
    ):
        uexp = assets / f"Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_{suffix}.uexp"
        uexp.parent.mkdir(parents=True, exist_ok=True)
        uexp.write_bytes(b"filepath://./" + movie.removeprefix("Client/Content/").encode() + b"\0")
        mp4 = assets / movie
        mp4.parent.mkdir(parents=True, exist_ok=True)
        mp4.write_bytes(b"mp4")

    m0394 = resolve_discovered_cutscene_sources(
        assets,
        "M0394",
        candidate_wrappers=scanned["M0394"]["wrappers"],
        candidate_mp4s=scanned["M0394"]["direct_mp4s"],
    )
    assert [item["asset"] for item in m0394] == [
        "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nv.M0394_Nv",
        "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nan.M0394_Nan",
    ]
    assert [item["rover"] for item in m0394] == ["female", "male"]

    cam = assets / "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4"
    cam.parent.mkdir(parents=True, exist_ok=True)
    cam.write_bytes(b"cam")
    m0396 = resolve_discovered_cutscene_sources(
        assets, "M0396", candidate_mp4s=scanned["M0396"]["direct_mp4s"]
    )
    assert len(m0396) == 1
    assert m0396[0]["asset"] == "asset:ue:/Game/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4"
    assert m0396[0]["embedded_fragment"] is True
    assert m0396[0]["rover"] is None


def test_plan_cutscene_supports_fallback_sources_and_fails_closed_when_absent(tmp_path):
    db_path = _create_empty_videodata(tmp_path / "db_VideoData.db")
    with pytest.raises(ValueError, match="Cutscene not found in VideoData"):
        plan_cutscene(db_path, tmp_path, "M0398", "3.8.0")

    recipe_m0396 = plan_cutscene(
        db_path,
        tmp_path,
        "M0396",
        "3.8.0",
        fallback_sources=[
            {
                "asset": "asset:ue:/Game/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4",
                "girl_or_boy": 2,
                "basis": "archive direct MP4",
            }
        ],
    )
    assert recipe_m0396["cutscene"] == "cutscene:M0396"
    assert recipe_m0396["compare_variants"] is False
    assert recipe_m0396["flow"]["nodes"] == [
        {
            "id": "clip-0",
            "kind": "clip",
            "asset": "asset:ue:/Game/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4",
            "segment": None,
            "start": 0,
            "end": None,
            "next": None,
        }
    ]

    recipe_m0394 = plan_cutscene(
        db_path,
        tmp_path,
        "M0394",
        "3.8.0",
        fallback_sources=[
            {
                "asset": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nv.M0394_Nv",
                "girl_or_boy": 0,
                "basis": "archive StreamMediaSource wrapper",
            },
            {
                "asset": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0394/M0394_Nan.M0394_Nan",
                "girl_or_boy": 1,
                "basis": "archive StreamMediaSource wrapper",
            },
        ],
    )
    assert recipe_m0394["compare_variants"] is True
    assert recipe_m0394["flow"]["nodes"][0]["kind"] == "choice"
    assert [opt["rover"] for opt in recipe_m0394["flow"]["nodes"][0]["options"]] == ["female", "male"]

    chained = plan_cutscene(
        db_path,
        tmp_path,
        "M0350B",
        "3.8.0",
        fallback_sources=[
            {"asset": "asset:ue:/Game/Aki/Movies/LevelA_Seq/03_3/M0350B.mp4", "girl_or_boy": 2},
            {"asset": "asset:ue:/Game/Aki/Movies/LevelA_Seq/03_3/M0350B_Cam002.mp4", "girl_or_boy": 2},
        ],
    )
    assert chained["compare_variants"] is False
    assert chained["flow"]["nodes"][0]["next"] == "clip-1"


@pytest.mark.asyncio
async def test_cutscene_assets_discovers_levela_archive_cutscenes_without_pure_engine_sequences(
    monkeypatch, tmp_path
):
    sources = {
        "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4": b"m0396 mp4",
        "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0397_Nanzhu.mp4": b"m0397 nan",
        "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0397_Nvzhu.mp4": b"m0397 nv",
        "Client/Content/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/DA_M0398.uasset": b"in-engine da",
    }
    config_files = tmp_path / "config/files"
    _create_empty_videodata(config_files / "db_VideoData.db")
    index = _create_archive_index(tmp_path / "archive-index.sqlite", list(sources))

    async def export(_root, _tool, filters):
        out = tmp_path / "exports" / hashlib.sha256(filters.encode()).hexdigest()
        receipt = out / "manifest.json"
        if filters == "ConfigDB":
            config_receipt = tmp_path / "config/manifest.json"
            config_receipt.write_text("{}")
            return config_receipt
        for name in filters.split("|"):
            if name in sources:
                p = out / "files" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(sources[name])
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text("{}")
        return receipt

    monkeypatch.setattr(release_media, "missing_cutscene_identities", AsyncMock(return_value=set()))
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    tool = tmp_path / "tool"
    tool.write_bytes(b"tool")
    monkeypatch.setattr(release_media, "tool_path", lambda _: tool)
    monkeypatch.setattr(release_media, "export_assets", export)
    monkeypatch.setattr(cutscene_sources, "export_assets", export)
    monkeypatch.setattr(release_media, "ensure_archive_index", AsyncMock(return_value=index))
    monkeypatch.setattr(release_media, "indexed_package_paths", lambda _i, _p: {})
    monkeypatch.setattr(release_media, "publish_inventory", AsyncMock(return_value={}))
    monkeypatch.setattr(release_media, "authored_cutscene_assets", AsyncMock(return_value={}))
    monkeypatch.setattr(release_media, "discover_bindata_cutscenes", AsyncMock(return_value={"cutscene_names": []}))
    monkeypatch.setattr(
        release_media,
        "levela_sequence_candidates",
        AsyncMock(
            return_value={
                "M0396": [{"sequence_asset": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0396/DA_M0396.DA_M0396"}],
                "M0397": [{"sequence_asset": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0397/DA_M0397.DA_M0397"}],
                "M0398": [{"sequence_asset": "asset:ue:/Game/Aki/Sequence/LevelA_Seq/Main/03_8/M0398/DA_M0398.DA_M0398"}],
            }
        ),
    )
    registered = AsyncMock(return_value={"registered": ["M0396", "M0397"], "edges": 6})
    monkeypatch.setattr(release_media, "register_discovered_cutscenes", registered)
    from wuwa_story_worker import voice_bootstrap

    monkeypatch.setattr(
        voice_bootstrap,
        "discover_video_plan",
        lambda *_a, **_kw: {"id": "plan-38", "files": []},
    )

    result, status = await release_media.cutscene_assets(
        {"targets": ["M0395"]},
        {"workspace_id": 11, "asset_version": "3.8.0", "tier": "hd", "release_id": 22},
        session=SimpleNamespace(),
    )
    assert status == "completed"
    assert result["discovered_cutscene_names"] == ["M0396", "M0397"]
    assert result["media_report"]["discovered_cutscenes"] == ["M0396", "M0397"]
    registered_payload = registered.await_args.args[2]
    assert [scene["cg_name"] for scene in registered_payload] == ["M0396", "M0397"]


@pytest.mark.asyncio
async def test_cutscene_imports_archive_fallback_when_not_in_videodata(monkeypatch, tmp_path):
    assets = tmp_path / "release-media/files"
    movie = assets / "Client/Content/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"cam006_012")
    _create_empty_videodata(assets / "db_VideoData.db")
    _create_archive_index(
        tmp_path / "archive-index.sqlite",
        ["Client/Content/Aki/Movies/LevelA_Seq/03_8/M0396_Cam006_012.mp4"],
    )

    monkeypatch.setattr(release_media, "missing_cutscene_identities", AsyncMock(return_value={"cutscene:M0396"}))
    monkeypatch.setattr(release_media, "client_root", lambda _: tmp_path)
    monkeypatch.setattr(release_media, "video_database", lambda p: p / "db_VideoData.db")
    tool = tmp_path / "tool"
    tool.write_bytes(b"tool")
    monkeypatch.setattr(release_media, "tool_path", lambda _: tool)
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"ffmpeg")
    monkeypatch.setattr(release_media.shutil, "which", lambda _n: str(ffmpeg))
    monkeypatch.setattr(release_media, "update_admin_run", AsyncMock())

    async def fake_frame_audio(*_args, **_kwargs):
        return [], {"diagnostics": [], "contexts": []}

    monkeypatch.setattr(release_media, "frame_audio_inputs", fake_frame_audio)
    from wuwa_story_worker import cutscene_music_context

    monkeypatch.setattr(cutscene_music_context, "prepare_music_context", AsyncMock())
    imported = AsyncMock(return_value={"videos": 1, "segments": 0, "flow": "entry"})
    monkeypatch.setattr(release_media, "import_cutscene_recipe", imported)

    result, status = await release_media.cutscene(
        {"targets": ["M0396"], "run_id": 99, "game_version": "3.8.0"},
        {"asset_version": "3.8.0"},
    )
    assert status == "completed"
    assert result["videos"] == 1
    imported.assert_awaited_once()
