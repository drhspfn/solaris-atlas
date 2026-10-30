import hashlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from botocore.exceptions import ClientError

from wuwa_story.ingestion import model_uploads as uploads


def job():
    return uploads.ModelUploadJob(run_id=1, character_key="character:1211", owner_node_id=2,
        release_id=3, game_version="3.3.0", archive_bytes=3, storage_token="a" * 32)


@pytest.mark.asyncio
async def test_chunk_upload_is_bounded_immutable_and_recorded_only_after_storage(monkeypatch):
    payload = job()
    run = NS(status="uploading", metadata_json={"uploaded_parts": []})
    monkeypatch.setattr(uploads, "upload_run", AsyncMock(return_value=(run, payload)))
    client = NS(put_object=Mock())
    monkeypatch.setattr(uploads, "S3Storage", lambda _: NS(client=client, bucket="test"))
    db = NS(commit=AsyncMock())

    async def body():
        yield b"abc"

    await uploads.store_chunk(db, 1, 0, body())
    assert client.put_object.call_args.kwargs["IfNoneMatch"] == "*"
    assert client.put_object.call_args.kwargs["Body"] == b"abc"
    assert run.metadata_json["uploaded_parts"] == [0]
    client.put_object.side_effect = ClientError({"ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
    client.head_object = Mock(return_value={"Metadata": {"sha256": hashlib.sha256(b"abc").hexdigest()}})
    await uploads.store_chunk(db, 1, 0, body())
    assert run.metadata_json["uploaded_parts"] == [0]
    client.head_object.return_value = {"Metadata": {"sha256": "wrong"}}
    with pytest.raises(ValueError, match="different data"):
        await uploads.store_chunk(db, 1, 0, body())

    async def too_large():
        yield b"abcd"

    client.put_object.reset_mock()
    with pytest.raises(ValueError, match="declared size"):
        await uploads.store_chunk(db, 1, 0, too_large())
    client.put_object.assert_not_called()
    run.status = "queued"
    with pytest.raises(ValueError, match="already closed"):
        await uploads.store_chunk(db, 1, 0, body())


@pytest.mark.asyncio
async def test_missing_chunks_never_queue_a_publication_and_completion_is_idempotent(monkeypatch):
    payload = job()
    run = NS(id=1, status="uploading", metadata_json={"uploaded_parts": []})
    monkeypatch.setattr(uploads, "upload_run", AsyncMock(return_value=(run, payload)))
    publish = AsyncMock()
    monkeypatch.setattr(uploads, "publish_media_job", publish)
    db = NS(commit=AsyncMock())
    with pytest.raises(ValueError, match="incomplete"):
        await uploads.finish_upload(db, 1)
    publish.assert_not_called()
    run.metadata_json["uploaded_parts"] = [0]
    assert await uploads.finish_upload(db, 1) is run
    assert run.status == "queued"
    await uploads.finish_upload(db, 1)
    publish.assert_awaited_once()
    assert publish.call_args.args[0] == payload.model_dump()


def test_all_upload_mutations_keep_admin_and_csrf_dependencies():
    from wuwa_story.api.routes.character_models import router
    from wuwa_story.auth.dependencies import require_admin, require_csrf
    paths = [route for route in router.routes if "/uploads" in route.path]
    assert len(paths) == 3
    for route in paths:
        calls = {dep.call for dep in route.dependant.dependencies}
        assert {require_admin, require_csrf} <= calls
