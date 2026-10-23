from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from wuwa_story.ingestion.entity_media import MediaRequest
from wuwa_story.ingestion.media_provenance import missing_media_targets


def target(path="/Game/Icon.Icon", language=None):
    return {"owner": 4, "release_id": 2, "path": path,
            "language": language, "kind": "voice" if language else "image"}


def row(value, **source):
    reference = SimpleNamespace(
        owner_node_id=value["owner"], release_id=value["release_id"],
        source_path=value["path"], source_name=value["language"],
        file_id=10,
        reference_type="character_voice" if value["language"] else "entity_image",
        metadata_json={"game_source": {"schema_version": 1,
            "package_path": value["path"].split(".")[0],
            "source_files": [{"package_path": "Client/Content/Icon.uasset", "sha256": "a" * 64}],
            "asset_version": "3.7.0", "download_id": "a" * 64, **source}},
    )
    return reference, SimpleNamespace(size_bytes=12), SimpleNamespace(object_key="objects/icon")


@pytest.mark.asyncio
@pytest.mark.parametrize("change,exists,missing", [
    ({}, 12, False), ({}, None, True), ({}, 10, True),
    ({"asset_version": "3.6.0"}, 12, True),
    ({"download_id": "b" * 64}, 12, True),
    ({"schema_version": 0}, 12, True), ({"source_files": []}, 12, True),
])
async def test_skip_requires_matching_provenance_and_live_blob(change, exists, missing):
    value = target()
    session = AsyncMock()
    session.execute.return_value = [row(value, **change)]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(
        return_value=None if exists is None else SimpleNamespace(size_bytes=exists)))
    result = await missing_media_targets(session, [value],
        source={"asset_version": "3.7.0", "download_id": "a" * 64}, storage=storage)
    assert result == ([value] if missing else [])
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_languages_and_shared_blobs_are_batched():
    values = [target(language=language) for language in ("en", "ja", "ko", "zh")]
    session = AsyncMock()
    session.execute.return_value = [row(value) for value in values[:2]]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    assert await missing_media_targets(session, values,
        source={"asset_version": "3.7.0"}, storage=storage) == values[2:]
    session.execute.assert_awaited_once()
    storage.stat.assert_awaited_once_with("objects/icon")


@pytest.mark.parametrize("workspace_id", [0, -1, True, "1"])
def test_workspace_id_requires_positive_integer(workspace_id):
    with pytest.raises(ValidationError):
        MediaRequest(download_id="a" * 64, asset_version="3.7.0", game_version="1.0",
                     entities=["character:1"], workspace_id=workspace_id)


@pytest.mark.asyncio
async def test_matching_client_is_reused_across_releases_after_live_verification():
    old, new = target(), {**target(), "release_id": 9}
    session = AsyncMock()
    session.add = Mock()
    session.execute.return_value = [row(old)]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    assert await missing_media_targets(session, [new], source={"asset_version": "3.7.0"},
                                       storage=storage) == []
    reference = session.add.call_args.args[0]
    assert reference.release_id == 9 and reference.file_id == 10
    assert reference.source_path == old["path"]
    assert reference.metadata_json["game_source"]["download_id"] == "a" * 64


@pytest.mark.asyncio
async def test_storage_outage_is_not_silently_reported_as_missing():
    session = AsyncMock()
    session.execute.return_value = [row(target())]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(side_effect=OSError))
    with pytest.raises(OSError):
        await missing_media_targets(session, [target()], source={}, storage=storage)


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [True, False])
async def test_availability_flags_follow_live_storage_not_stale_database_state(exists):
    reference, file, location = row(target())
    location.available = not exists
    session = AsyncMock()
    session.execute.return_value = [(reference, file, location)]
    storage = SimpleNamespace(backend="s3", bucket="media", stat=AsyncMock(
        return_value=SimpleNamespace(size_bytes=12) if exists else None))
    result = await missing_media_targets(session, [target()], source={}, storage=storage)
    assert result == ([] if exists else [target()])
    assert location.available is exists and location.verified_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_tool,missing", [("a" * 64, False), ("b" * 64, True)])
async def test_changed_tool_version_requires_export(stored_tool, missing):
    session = AsyncMock()
    session.execute.return_value = [row(target(), tools={"WUWA_FMODEL_PATH": stored_tool})]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    result = await missing_media_targets(session, [target()],
        source={"tools": {"WUWA_FMODEL_PATH": "a" * 64}}, storage=storage)
    assert result == ([target()] if missing else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,reference_type,path,source_name", [
    ("voice", "voice_audio", "Client/Content/Audio/en_vo_plot_F.wem", "en_vo_plot_F.wem"),
    ("video", "cutscene_video", "Client/Content/Aki/Movies/CG.mp4", "main"),
    ("image", "event_banner", "upstream-banner-alias", None),
    ("image", "map_texture", "/Game/Map/Terrain.Terrain", "layer:1"),
])
async def test_generic_media_identity_preserves_filenames_roles_and_package_aliases(
    kind, reference_type, path, source_name,
):
    value = {"kind": kind, "reference_type": reference_type, "owner": None,
             "release_id": 9, "path": path, "source_name": source_name,
             "package_path": "Client/Content/exact-package.uasset"}
    reference = SimpleNamespace(
        owner_node_id=None, release_id=9, source_path=path, source_name=source_name,
        reference_type=reference_type, file_id=10, metadata_json={"game_source": {
            "schema_version": 1, "package_path": value["package_path"],
            "source_files": [{"package_path": value["package_path"], "sha256": "a" * 64}],
            "recipe_sha256": "b" * 64,
        }},
    )
    session = AsyncMock()
    session.execute.return_value = [(reference, SimpleNamespace(size_bytes=12),
                                     SimpleNamespace(object_key="objects/file"))]
    storage = SimpleNamespace(backend="s3", bucket="media",
                             stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    assert await missing_media_targets(session, [value],
        source={"recipe_sha256": "b" * 64}, storage=storage) == []
    assert await missing_media_targets(session, [value],
        source={"recipe_sha256": "c" * 64}, storage=storage) == [value]
