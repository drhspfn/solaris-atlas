from pathlib import Path
from unittest.mock import Mock

import pytest

from wuwa_story.config.settings import Settings
from wuwa_story.storage.s3 import S3Storage


def storage(monkeypatch, **settings):
    monkeypatch.setattr("wuwa_story.storage.s3.boto3.client", lambda *a, **kw: Mock())
    return S3Storage(Settings(_env_file=None, **settings))


def test_public_urls_support_minio_and_bucketless_cdn(monkeypatch):
    local = storage(monkeypatch, s3_endpoint_url="http://minio:9000",
                    s3_public_endpoint_url="http://localhost:9000")
    assert local.public_url("objects/a b/日本?.png") == (
        "http://localhost:9000/wuwa/objects/a%20b/%E6%97%A5%E6%9C%AC%3F.png")
    cdn = storage(monkeypatch, media_public_base_url="https://media.example.com/")
    assert cdn.public_url("objects/ab/cd/hash") == "https://media.example.com/objects/ab/cd/hash"
    cdn.public_client.generate_presigned_url.assert_not_called()


@pytest.mark.asyncio
async def test_only_content_addressed_uploads_are_immutable(monkeypatch, tmp_path: Path):
    backend = storage(monkeypatch)
    backend.client.head_object.return_value = {"ContentLength": 3}
    source = tmp_path / "image.png"
    source.write_bytes(b"png")
    await backend.put_file(source, "objects/ab/cd/hash", "image/png")
    assert backend.client.upload_file.call_args.kwargs["ExtraArgs"] == {
        "ContentType": "image/png", "CacheControl": "public, max-age=31536000, immutable"}
    await backend.put_file(source, "mutable/latest.png", "image/png")
    assert "CacheControl" not in backend.client.upload_file.call_args.kwargs["ExtraArgs"]


def test_private_signing_remains_explicit(monkeypatch):
    backend = storage(monkeypatch)
    backend.signed_url("private/object", expires=60)
    backend.public_client.generate_presigned_url.assert_called_once_with(
        "get_object", Params={"Bucket": "wuwa", "Key": "private/object"}, ExpiresIn=60)


@pytest.mark.asyncio
async def test_existing_metadata_backfill_is_safe_and_idempotent(monkeypatch):
    backend = storage(monkeypatch)
    backend.client.head_object.return_value = {
        "ContentType": "audio/ogg", "Metadata": {"source": "game"}, "ContentLength": 42,
    }
    assert await backend.refresh_public_cache_header("objects/hash")
    backend.client.copy_object.assert_not_called()
    assert await backend.refresh_public_cache_header("objects/hash", apply=True)
    args = backend.client.copy_object.call_args.kwargs
    assert args["ContentType"] == "audio/ogg" and args["Metadata"] == {"source": "game"}
    assert args["CacheControl"] == backend.cache_control
    backend.client.head_object.return_value["CacheControl"] = backend.cache_control
    assert not await backend.refresh_public_cache_header("objects/hash", apply=True)
    with pytest.raises(ValueError):
        await backend.refresh_public_cache_header("private/file", apply=True)
