from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size_bytes: int
    etag: str | None = None


class ObjectStorage(Protocol):
    backend: str
    bucket: str | None

    async def put_file(
        self, source: Path, object_key: str, content_type: str | None = None
    ) -> ObjectInfo: ...
    async def get(self, object_key: str, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]: ...
    async def exists(self, object_key: str) -> bool: ...
    async def stat(self, object_key: str) -> ObjectInfo | None: ...
    async def delete(self, object_key: str) -> None: ...
