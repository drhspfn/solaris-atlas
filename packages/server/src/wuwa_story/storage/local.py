import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath

from wuwa_story.storage.base import ObjectInfo


def _safe_target(root: Path, object_key: str) -> Path:
    key = PurePosixPath(object_key)
    if key.is_absolute() or not key.parts or any(part in ("", ".", "..") for part in key.parts):
        raise ValueError("object_key must be a normalized relative POSIX path")
    target = root.joinpath(*key.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("object_key escapes the local storage root")
    return target


class LocalStorage:
    backend = "local"
    bucket = None

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    async def put_file(
        self, source: Path, object_key: str, content_type: str | None = None
    ) -> ObjectInfo:
        del content_type
        return await asyncio.to_thread(self._put_file, source, object_key)

    def _put_file(self, source: Path, object_key: str) -> ObjectInfo:
        target = _safe_target(self.root, object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return ObjectInfo(target.stat().st_size)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".upload-", delete=False) as tmp:
            temp_path = Path(tmp.name)
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, tmp, length=1024 * 1024)
        try:
            temp_path.replace(target)
        finally:
            temp_path.unlink(missing_ok=True)
        return ObjectInfo(target.stat().st_size)

    async def get(self, object_key: str, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        path = _safe_target(self.root, object_key)
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(handle.read, chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def exists(self, object_key: str) -> bool:
        return await asyncio.to_thread(_safe_target(self.root, object_key).is_file)

    async def stat(self, object_key: str) -> ObjectInfo | None:
        path = _safe_target(self.root, object_key)
        if not await asyncio.to_thread(path.is_file):
            return None
        size = await asyncio.to_thread(lambda: path.stat().st_size)
        return ObjectInfo(size)

    async def delete(self, object_key: str) -> None:
        path = _safe_target(self.root, object_key)
        await asyncio.to_thread(path.unlink, missing_ok=True)
