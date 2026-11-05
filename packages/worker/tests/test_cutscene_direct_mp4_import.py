"""Direct VideoData MP4s must publish without manufactured Unreal wrappers."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import cutscene_import


@pytest.mark.asyncio
async def test_direct_mp4_import_retains_original_provenance_without_reading_wrapper(tmp_path, monkeypatch):
    source = "/Game/Aki/Movies/Storyboard/R0012.mp4"
    asset = "asset:ue:" + source
    relative = "Client/Content/Aki/Movies/Storyboard/R0012.mp4"
    movie = tmp_path / relative
    movie.parent.mkdir(parents=True)
    original = b"authored original MP4"
    movie.write_bytes(original)
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({
        "cutscene": "cutscene:R0012", "asset_version": "3.7.1", "videos": [{"asset": asset}],
        "flow": {"entry": "whole", "nodes": [{"id": "whole", "kind": "clip", "asset": asset}],
                 "evidence": "VideoData direct MP4 reference"},
    }))
    context = {"asset_version": "3.7.1", "download_id": "a" * 64, "tier": "hd",
               "tools": {"WUWA_FMODEL_PATH": "b" * 64}}
    monkeypatch.setattr(cutscene_import, "missing_cutscene_identities", AsyncMock(return_value={"cutscene:R0012"}))
    complete = AsyncMock()
    monkeypatch.setattr(cutscene_import, "complete_cutscene_publication", complete)
    read_bytes = Path.read_bytes
    def read_without_wrappers(path):
        assert path.suffix.casefold() not in {".uexp", ".uasset"}, "direct MP4 has no wrapper"
        return read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", read_without_wrappers)
    def reject_wrapper_parser(_data):
        raise AssertionError("direct MP4 must not invoke the wrapper parser")
    monkeypatch.setattr(cutscene_import, "movie_path", reject_wrapper_parser)
    monkeypatch.setattr(cutscene_import, "video_duration", lambda _ffmpeg, path: (2.0, False))
    async def convert(command, **_kwargs):
        assert Path(command[command.index("-i") + 1]) == movie
        Path(command[-1]).write_bytes(b"playable MP4")
    monkeypatch.setattr(cutscene_import, "run_tool", convert)
    storage = AsyncMock()
    monkeypatch.setattr(cutscene_import, "get_settings", lambda: None)
    monkeypatch.setattr(cutscene_import, "S3Storage", lambda _: storage)
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.scalars.side_effect = [
        [SimpleNamespace(id=1, canonical_key="cutscene:R0012"), SimpleNamespace(id=2, canonical_key=asset)],
        [10], [2],
    ]
    monkeypatch.setattr(cutscene_import, "SessionFactory", lambda: session)
    service = AsyncMock()
    files, references = [], []
    async def register_file(_session, path, kind, **_kwargs):
        files.append((path, kind, path.read_bytes()))
        return SimpleNamespace(id=len(files))
    async def reference(*args):
        ref = SimpleNamespace(id=len(references) + 1, kind=args[4])
        references.append(ref)
        return ref
    service.register_file.side_effect = register_file
    monkeypatch.setattr(cutscene_import, "FileRegistrationService", lambda _: service)
    monkeypatch.setattr(cutscene_import, "publication_reference", reference)

    result = await cutscene_import.import_cutscene_recipe(
        recipe, tmp_path, tmp_path, tmp_path, tmp_path / "work", tmp_path / "decoder", tmp_path / "ffmpeg",
        source_context=context)

    assert result["videos"] == 1
    assert [(path, data) for path, kind, data in files if kind == "video_game"] == [(movie, original)]
    video = next(ref for ref in references if ref.kind == "cutscene_video")
    assert video.source_path == source and video.source_name == "R0012.mp4"
    provenance = video.metadata_json["game_source"]
    # Keep the existing canonical package key; the source file retains the MP4 extension.
    assert provenance["package_path"] == "/Game/Aki/Movies/Storyboard/R0012"
    assert provenance["download_id"] == context["download_id"]
    assert provenance["asset_version"] == "3.7.1"
    assert provenance["source_files"] == [{"package_path": relative, "sha256": hashlib.sha256(original).hexdigest()}]
    assert provenance["recipe"]["videos"][0]["asset"] == asset
    assert video.metadata_json["source_file_ids"] == []
    assert service.register_variant.return_value.metadata_json["game_source"] == provenance
    complete.assert_awaited_once()
    assert complete.await_args.args[1] == {asset: provenance}
    assert complete.await_args.kwargs["complete"] is True
    session.commit.assert_awaited_once()
