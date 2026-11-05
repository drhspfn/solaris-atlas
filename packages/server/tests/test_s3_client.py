from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import Mock

import pytest

from wuwa_story.config.settings import Settings
from wuwa_story.storage import s3


def settings():
    return Settings(
        _env_file=None, s3_endpoint_url="https://storage.example.test",
        s3_access_key_id="test-access", s3_secret_access_key="test-secret",
        s3_use_ssl=True,
    )


def test_concurrent_first_access_initializes_one_private_session_and_client(monkeypatch):
    start = Barrier(9)
    entered, duplicate, release = Event(), Event(), Event()
    client = Mock()

    def construct_client(*args, **kwargs):
        if entered.is_set():
            duplicate.set()
        entered.set()
        assert release.wait(5), "initialization gate was not released"
        return client

    session = Mock(client=Mock(side_effect=construct_client))
    create_session = Mock(return_value=session)
    monkeypatch.setattr(s3.boto3.session, "Session", create_session)
    monkeypatch.setattr(s3.boto3, "client", Mock(side_effect=AssertionError("default session")))
    storage = s3.S3Storage(settings())

    def access():
        start.wait(5)
        return storage.client

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(access) for _ in range(8)]
        try:
            start.wait(5)
            assert entered.wait(5)
            assert not duplicate.wait(0.1)
        finally:
            release.set()
        assert all(future.result(timeout=5) is client for future in futures)
    create_session.assert_called_once_with()
    session.client.assert_called_once()


def test_storage_instances_have_distinct_sessions_and_preserve_tls_and_retries(monkeypatch):
    first, second = Mock(), Mock()
    create_session = Mock(side_effect=[first, second])
    monkeypatch.setattr(s3.boto3.session, "Session", create_session)
    one, two = s3.S3Storage(settings()), s3.S3Storage(settings())
    create_session.assert_not_called()
    assert one.client is first.client.return_value
    assert two.client is second.client.return_value
    assert one.client is not two.client
    assert create_session.call_count == 2
    for session in (first, second):
        kwargs = session.client.call_args.kwargs
        assert session.client.call_args.args == ("s3",)
        assert kwargs["endpoint_url"] == "https://storage.example.test"
        assert kwargs["use_ssl"] is True
        assert "verify" not in kwargs  # Keep the SDK's certificate-verification default.
        assert kwargs["aws_access_key_id"] == "test-access"
        assert kwargs["aws_secret_access_key"] == "test-secret"
        config = kwargs["config"]
        assert (config.connect_timeout, config.read_timeout, config.max_pool_connections) == (5, 30, 20)
        assert config.retries == {"mode": "standard", "total_max_attempts": 3}


def test_failed_initialization_releases_lock_and_can_retry(monkeypatch):
    failed = Mock(client=Mock(side_effect=RuntimeError("initialization failed")))
    succeeded = Mock()
    create_session = Mock(side_effect=[failed, succeeded])
    monkeypatch.setattr(s3.boto3.session, "Session", create_session)
    storage = s3.S3Storage(settings())
    with pytest.raises(RuntimeError, match="initialization failed"):
        _ = storage.client
    # A second thread must be able to initialize after the failed attempt.
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(lambda: storage.client).result(timeout=5) is succeeded.client.return_value
    assert create_session.call_count == 2


@pytest.mark.asyncio
async def test_upload_disables_managed_threads_and_preserves_metadata(monkeypatch, tmp_path):
    client = Mock()
    client.head_object.return_value = {"ContentLength": 4, "ETag": '"verified"'}
    monkeypatch.setattr(s3.boto3.session, "Session", Mock(return_value=Mock(client=Mock(return_value=client))))
    storage = s3.S3Storage(settings())
    source = tmp_path / "voice.wav"
    source.write_bytes(b"wave")
    result = await storage.put_file(source, "objects/hash", "audio/wav")
    kwargs = client.upload_file.call_args.kwargs
    assert kwargs["Config"].use_threads is False
    assert kwargs["ExtraArgs"] == {
        "ContentType": "audio/wav", "CacheControl": storage.cache_control,
    }
    assert client.upload_file.call_args.args == (str(source), storage.bucket, "objects/hash")
    assert (result.size_bytes, result.etag) == (4, "verified")


@pytest.mark.asyncio
async def test_upload_errors_are_not_swallowed(monkeypatch, tmp_path):
    client = Mock(upload_file=Mock(side_effect=OSError("upload failed")))
    monkeypatch.setattr(s3.boto3.session, "Session", Mock(return_value=Mock(client=Mock(return_value=client))))
    storage = s3.S3Storage(settings())
    source = tmp_path / "voice.wav"
    source.write_bytes(b"wave")
    with pytest.raises(OSError, match="upload failed"):
        await storage.put_file(source, "objects/hash", "audio/wav")
    client.head_object.assert_not_called()
