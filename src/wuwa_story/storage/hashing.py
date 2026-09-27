import hashlib
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> tuple[bytes, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
            size += len(chunk)
    return digest.digest(), size


def content_object_key(digest: bytes) -> str:
    hex_digest = digest.hex()
    return f"objects/{hex_digest[:2]}/{hex_digest[2:4]}/{hex_digest}"
