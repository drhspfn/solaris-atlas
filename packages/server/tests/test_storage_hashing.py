from pathlib import Path

import pytest

from wuwa_story.storage.hashing import content_object_key, sha256_file
from wuwa_story.storage.local import LocalStorage, _safe_target


def test_hash_and_content_addressed_key(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"narrative evidence")

    digest, size = sha256_file(source)

    assert size == len(b"narrative evidence")
    assert (
        content_object_key(digest)
        == f"objects/{digest.hex()[:2]}/{digest.hex()[2:4]}/{digest.hex()}"
    )


def test_local_storage_round_trip_and_idempotent_put(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"payload")
    storage = LocalStorage(tmp_path / "store")

    first = storage._put_file(source, "objects/ab/item")
    second = storage._put_file(source, "objects/ab/item")

    assert first.size_bytes == second.size_bytes == 7
    assert (tmp_path / "store/objects/ab/item").read_bytes() == b"payload"


def test_local_storage_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _safe_target(tmp_path, "../outside")
    with pytest.raises(ValueError):
        _safe_target(tmp_path, "/absolute")
