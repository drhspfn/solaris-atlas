from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.visual_memory.cli import bootstrap
from wuwa_story.visual_memory.images import MAX_IMAGE_BYTES


class FakeSession:
    def __init__(self, identities, rows):
        self.identities = iter(identities)
        self.rows = iter(rows)
        self.queries = []

    async def scalars(self, statement):
        self.queries.append(statement)
        return SimpleNamespace(all=lambda: next(self.identities))

    async def execute(self, statement):
        self.queries.append(statement)
        return SimpleNamespace(first=lambda: next(self.rows))


class FakeStorage:
    backend = "s3"
    bucket = "game"

    def __init__(self, data=b"image"):
        self.data = data
        self.keys = []

    async def get(self, key):
        self.keys.append(key)
        yield self.data


def asset(size=5):
    return (
        SimpleNamespace(id=12, reference_type="character_icon"),
        SimpleNamespace(id=34, size_bytes=size),
        SimpleNamespace(object_key="objects/asset"),
    )


@pytest.mark.asyncio
async def test_bootstrap_requires_unique_explicit_identity_and_preserves_duplicate():
    session = FakeSession([[7], [1, 2], []], [asset()])
    storage = FakeStorage()
    service = SimpleNamespace(add_reference=AsyncMock(return_value={"id": 91, "duplicate": True}))
    result = await bootstrap(session, storage, service, [10, 20, 30])
    assert [item["status"] for item in result["items"]] == [
        "duplicate",
        "identity_missing_or_ambiguous",
        "identity_missing_or_ambiguous",
    ]
    assert storage.keys == ["objects/asset"]
    kwargs = service.add_reference.await_args.kwargs
    assert kwargs["links"] == [{"identity_id": 7, "appearance_id": None}]
    assert kwargs["status"] == "pending" and kwargs["source_type"] == "game_asset"
    assert "file_reference_id=12" in kwargs["rights"] and "rights not verified" in kwargs["rights"]
    query = str(session.queries[1])
    assert (
        "owner_node_id" in query
        and "mime_type" in query
        and "backend" in query
        and "bucket" in query
    )


@pytest.mark.asyncio
async def test_bootstrap_bounds_and_missing_images():
    service = SimpleNamespace(add_reference=AsyncMock())
    for node_ids in ([], list(range(1, 22)), [1, 1], [0], [True]):
        with pytest.raises(ValueError):
            await bootstrap(None, None, service, node_ids)
    session = FakeSession([[1], [2]], [None, asset(MAX_IMAGE_BYTES + 1)])
    storage = FakeStorage()
    result = await bootstrap(session, storage, service, [1, 2])
    assert [item["status"] for item in result["items"]] == ["image_missing", "image_too_large"]
    assert not storage.keys
    service.add_reference.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_rejects_stream_exceeding_limit():
    session = FakeSession([[1]], [asset(size=None)])
    service = SimpleNamespace(add_reference=AsyncMock())
    with pytest.raises(ValueError, match="exceeds 8 MiB"):
        await bootstrap(session, FakeStorage(b"x" * (MAX_IMAGE_BYTES + 1)), service, [1])
    service.add_reference.assert_not_awaited()
