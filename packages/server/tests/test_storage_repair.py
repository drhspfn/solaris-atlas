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


@pytest.mark.asyncio
async def test_prepared_file_publishes_without_network_inside_transaction(tmp_path):
    path = tmp_path / "voice.wav"
    path.write_bytes(b"data")
    storage = SimpleNamespace(backend="fixture", bucket="test",
                              stat=AsyncMock(return_value=None),
                              put_file=AsyncMock(return_value=ObjectInfo(4, "uploaded")))
    registration = FileRegistrationService(storage)
    prepared = await registration.prepare_file(path, "audio/wav")
    storage.stat.side_effect = AssertionError("network inside DB publication")
    storage.put_file.side_effect = AssertionError("network inside DB publication")
    file = SimpleNamespace(id=9, file_type_id=1, size_bytes=4)
    location = SimpleNamespace(file_id=9, available=False)
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[SimpleNamespace(id=1), file, location]))
    assert await registration.register_file(session, path, "audio_wav", prepared=prepared) is file


@pytest.mark.asyncio
async def test_failed_upload_never_inserts_a_file_identity(tmp_path):
    path = tmp_path / "voice.wav"
    path.write_bytes(b"data")
    storage = SimpleNamespace(stat=AsyncMock(return_value=None),
                              put_file=AsyncMock(side_effect=OSError("upload failed")))
    session = SimpleNamespace(scalar=AsyncMock(), execute=AsyncMock())
    with pytest.raises(OSError, match="upload failed"):
        await FileRegistrationService(storage).register_file(session, path, "audio_wav")
    session.scalar.assert_not_awaited()
    session.execute.assert_not_awaited()

@pytest.mark.asyncio
async def test_variant_insert_tolerates_another_publisher_winning():
    variant = SimpleNamespace(parent_file_id=1, child_file_id=2, variant_type="voice_pcm_wav")
    session = SimpleNamespace(get=AsyncMock(side_effect=[None, variant]), execute=AsyncMock())
    result = await FileRegistrationService(SimpleNamespace()).register_variant(session, 1, 2, "voice_pcm_wav")
    assert result is variant
    assert "ON CONFLICT" in str(session.execute.call_args.args[0])
