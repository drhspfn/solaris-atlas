from types import SimpleNamespace
from unittest.mock import AsyncMock

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
