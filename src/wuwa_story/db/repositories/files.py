from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.storage import FileLocation, FileObject


async def get_file_by_sha256(session: AsyncSession, digest: bytes) -> FileObject | None:
    return await session.scalar(select(FileObject).where(FileObject.sha256 == digest))


async def list_file_locations(session: AsyncSession, file_id: int) -> list[FileLocation]:
    result = await session.scalars(
        select(FileLocation)
        .where(FileLocation.file_id == file_id)
        .order_by(FileLocation.is_primary.desc(), FileLocation.id)
    )
    return list(result)
