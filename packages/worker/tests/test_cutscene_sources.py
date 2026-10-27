from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import cutscene_sources
from wuwa_story_worker.archive_index import index_voice_paths


def archive_index(tmp_path, paths):
    log = tmp_path / "archive-index.log"
    log.write_text("".join(f"[File] {path}\n" for path in paths) + f"[Done] Listed {len(paths)} files\n")
    return index_voice_paths(log)


def test_movie_reference_comes_from_wrapper_bytes_not_cg_or_wrapper_name(tmp_path):
    wrapper = "Client/Content/Aki/Sequence/Storyboard/M0241.uasset"
    path = tmp_path / wrapper.replace(".uasset", ".uexp")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"prefix filepath://./Aki/Movies/Storyboard/NativeOtherName.mp4\0 suffix")
    assert cutscene_sources.authored_movie_paths(tmp_path, [wrapper]) == {
        wrapper: "Client/Content/Aki/Movies/Storyboard/NativeOtherName.mp4"}


def test_movie_lookup_requires_full_exact_path_and_preserves_archive_case(tmp_path):
    expected = "Client/Content/Aki/Movies/Storyboard/M0241.mp4"
    exact = "Client/Content/Aki/Movies/Storyboard/M0241.MP4"
    index = archive_index(tmp_path, [exact,
        "Client/Content/Aki/Movies/Other/M0241.mp4",
        "Client/Content/Aki/Movies/Storyboard/M02410.mp4"])
    assert cutscene_sources.indexed_movie_paths(index, [expected]) == {expected: exact}
    assert cutscene_sources.indexed_movie_paths(index, [
        "Client/Content/Aki/Movies/Storyboard/M02410_extra.mp4"]) == {}


@pytest.mark.asyncio
async def test_base_movies_export_only_confirmed_paths_in_one_batch(tmp_path, monkeypatch):
    wrappers, movies = [], []
    for number in (241, 245):
        wrapper = f"Client/Content/Aki/Sequence/Storyboard/M{number:04}.uasset"
        movie = f"Client/Content/Aki/Movies/Storyboard/Actual{number}.mp4"
        path = tmp_path / wrapper.replace(".uasset", ".uexp")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"filepath://./" + movie.removeprefix("Client/Content/").encode() + b"\0")
        wrappers.append(wrapper)
        movies.append(movie)
    index = archive_index(tmp_path, movies + ["Client/Content/Aki/Movies/Storyboard/Actual241_extra.mp4"])
    export = AsyncMock(return_value=tmp_path / "receipt.json")
    monkeypatch.setattr(cutscene_sources, "export_assets", export)
    result = await cutscene_sources.prepare_base_movies(tmp_path, tmp_path, wrappers, tmp_path / "fmodel", index=index)
    assert result == {"receipts": [tmp_path / "receipt.json"], "movies": dict(zip(wrappers, movies, strict=True)), "missing": []}
    export.assert_awaited_once()
    assert set(export.await_args.args[2].split("|")) == set(movies)


@pytest.mark.asyncio
async def test_absent_base_movie_is_reported_for_video_package_resolution(tmp_path, monkeypatch):
    wrapper = "Client/Content/Aki/Sequence/M0241.uasset"
    path = tmp_path / wrapper.replace(".uasset", ".uexp")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"filepath://./Aki/Movies/Storyboard/M0241.mp4\0")
    index = archive_index(tmp_path, ["Client/Content/Aki/Movies/WrongFolder/M0241.mp4"])
    export = AsyncMock()
    monkeypatch.setattr(cutscene_sources, "export_assets", export)
    result = await cutscene_sources.prepare_base_movies(tmp_path, tmp_path, [wrapper], tmp_path / "fmodel", index=index)
    assert result["missing"] == ["Client/Content/Aki/Movies/Storyboard/M0241.mp4"]
    assert result["receipts"] == []
    export.assert_not_awaited()


@pytest.mark.parametrize("wrapper", ["../escape.uasset", "/absolute.uasset", "Client/Content/Aki/../escape.uasset"])
def test_wrapper_paths_cannot_escape_export_root(tmp_path, wrapper):
    with pytest.raises(ValueError):
        cutscene_sources.authored_movie_paths(tmp_path, [wrapper])


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [False, True])
async def test_preparation_isolates_invalid_sources_and_reports_them(tmp_path, monkeypatch, valid):
    wrappers = {
        "Missing": None,
        "NoMovie": b"authored sequence without a movie reference",
        "Unsafe": b"filepath://./Aki/Movies/..\\escape.mp4\0",
        "Ambiguous": b"filepath://./Aki/Movies/A.mp4\0filepath://./Aki/Movies/B.mp4\0",
    }
    if valid:
        wrappers["Good"] = b"filepath://./Aki/Movies/LevelB_Seq\\02_7\\Actual.mp4\0"
    packages = []
    for name, data in wrappers.items():
        package = f"Client/Content/Aki/Sequence/{name}.uasset"
        packages.append(package)
        if data is not None:
            path = tmp_path / package.replace(".uasset", ".uexp")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    packages.append("Client/Content/Aki/../escape.uasset")
    movie = "Client/Content/Aki/Movies/LevelB_Seq/02_7/Actual.mp4"
    index = archive_index(tmp_path, [movie])
    export = AsyncMock(return_value=tmp_path / "receipt.json")
    monkeypatch.setattr(cutscene_sources, "export_assets", export)
    ensure_index = AsyncMock(side_effect=AssertionError("No index needed for rejected sources"))
    monkeypatch.setattr(cutscene_sources, "ensure_archive_index", ensure_index)
    result = await cutscene_sources.prepare_base_movies(
        tmp_path, tmp_path, packages, tmp_path / "fmodel", index=index if valid else None)
    expected = {"Client/Content/Aki/Sequence/Good.uasset": movie} if valid else {}
    assert result["movies"] == expected
    assert set(result["errors"]) == set(packages) - set(expected)
    assert "Expected one safe authored MP4 path" in result["errors"].values()
    assert result["missing"] == []
    assert result["receipts"] == ([tmp_path / "receipt.json"] if valid else [])
    if valid:
        export.assert_awaited_once_with(tmp_path, tmp_path / "fmodel", movie)
    else:
        export.assert_not_awaited()
    ensure_index.assert_not_awaited()
