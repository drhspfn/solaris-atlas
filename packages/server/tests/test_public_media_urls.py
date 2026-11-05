from unittest.mock import Mock

import pytest

from wuwa_story.config.settings import Settings
from wuwa_story.storage import s3


@pytest.mark.asyncio
async def test_public_urls_do_not_initialize_sdk_and_object_reads_reuse_one_client(monkeypatch):
    client = Mock()
    client.head_object.return_value = {"ContentLength": 123, "ETag": '"content-hash"'}
    create = Mock(return_value=client)
    session = Mock(client=create)
    new_session = Mock(return_value=session)
    monkeypatch.setattr(s3.boto3.session, "Session", new_session)
    storage = s3.S3Storage(Settings(media_public_base_url="https://cdn.example.test/"))
    assert storage.public_url("objects/path with spaces.png") == (
        "https://cdn.example.test/objects/path%20with%20spaces.png"
    )
    create.assert_not_called()
    new_session.assert_not_called()
    assert (await storage.stat("objects/one")).size_bytes == 123
    assert (await storage.stat("objects/two")).size_bytes == 123
    create.assert_called_once()
    new_session.assert_called_once_with()
    assert client.head_object.call_count == 2
