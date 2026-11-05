"""Search and cursor pagination for a character's published connections."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.session import get_session
from wuwa_story.read_models.readers.character_connections import character_connection_page

router = APIRouter()


@router.get("/characters/{canonical_key:path}/connections")
async def character_connections(
    canonical_key: str,
    section: Literal["quests", "characters"] = "quests",
    locale: str = "en",
    game_version: str | None = None,
    q: str = Query("", max_length=200),
    cursor: str | None = Query(None, max_length=2048),
    limit: int = Query(12, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    return await character_connection_page(
        session, canonical_key, section, locale=locale, game_version=game_version,
        limit=limit, q=q, cursor=cursor,
    )
