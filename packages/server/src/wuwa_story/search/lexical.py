from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.repositories.search import lexical_search


async def search(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 20,
    category: str | None = None,
    locale_id: int | None = None,
) -> list[dict[str, Any]]:
    return await lexical_search(session, query, limit=limit, category=category, locale_id=locale_id)
