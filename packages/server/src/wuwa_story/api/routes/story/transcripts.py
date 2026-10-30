"""Dialogue search and quest transcript endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.api.public_addresses import public_addresses
from wuwa_story.db.repositories.nodes import get_node
from wuwa_story.db.session import get_session
from wuwa_story.story import usecases
from wuwa_story.story.domain import StoryBrowsingError

router = APIRouter(tags=["story browsing"])


@router.get("/dialogue/search")
@public_addresses
async def search_dialogue(
    q: str = Query(min_length=1, max_length=512),
    character: str | None = None,
    quest_id: int | None = None,
    locale: str = "en",
    game_version: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if read_projections.should_route_to_projection():
        return await read_projections.dialogue_search_page(session, q, character, quest_id, locale, game_version, limit, offset)
    try:
        return await usecases.search_dialogue(
            session,
            q,
            character,
            quest_id,
            locale,
            game_version,
            limit,
            offset,
            node_lookup=get_node,
        )
    except StoryBrowsingError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get("/quests/{game_quest_id}/transcript")
@public_addresses
async def quest_transcript(
    game_quest_id: str,
    character: str | None = None,
    q: str | None = Query(default=None, max_length=512),
    locale: str = "en",
    game_version: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    focus_line: str | None = Query(default=None, max_length=512),
    include_raw: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if read_projections.should_route_to_projection():
        return await read_projections.transcript_page(
            session, game_quest_id, character, q, locale, game_version, limit, offset, focus_line, include_raw,
        )
    try:
        return await usecases.quest_transcript(
            session,
            game_quest_id,
            character,
            q,
            locale,
            game_version,
            limit,
            offset,
            focus_line,
            include_raw,
            node_lookup=get_node,
        )
    except StoryBrowsingError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
