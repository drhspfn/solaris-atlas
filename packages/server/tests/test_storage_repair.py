import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.storage.base import ObjectInfo
from wuwa_story.storage.service import FileRegistrationService


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, ObjectInfo(size_bytes=1), ObjectInfo(size_bytes=4)])
async def test_reimport_restores_missing_or_truncated_blob_and_availability(tmp_path, stored):
    path = tmp_path / "image.png"
    path.write_bytes(b"data")
    file = SimpleNamespace(id=9, file_type_id=1, size_bytes=4,
                           sha256=hashlib.sha256(b"data").digest())
    location = SimpleNamespace(file_id=9, available=False, verified_at=None, etag="old")
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[SimpleNamespace(id=1), file, location]))
    storage = SimpleNamespace(backend="fixture", bucket="test",
                              stat=AsyncMock(return_value=stored),
                              put_file=AsyncMock(return_value=ObjectInfo(size_bytes=4, etag="repaired")))
    result = await FileRegistrationService(storage).register_file(session, path, "image")
    assert result is file
    assert location.available is True and location.verified_at is not None
    if stored is None or stored.size_bytes != 4:
        storage.put_file.assert_awaited_once()
        assert location.etag == "repaired"
    else:
        storage.put_file.assert_not_awaited()
