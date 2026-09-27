from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.ops import GameRelease


async def list_releases(session: AsyncSession, limit: int = 100) -> list[GameRelease]:
    result = await session.scalars(
        select(GameRelease).order_by(GameRelease.sequence.desc()).limit(limit)
    )
    return list(result)


async def get_release(session: AsyncSession, release_id: int) -> GameRelease | None:
    return await session.get(GameRelease, release_id)
