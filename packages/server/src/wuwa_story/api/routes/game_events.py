"""Public, historical in-game event schedule."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.session import get_session

# Preserve helper imports used by existing event/notice consumers and tests.
from wuwa_story.events.service import (
    _artwork,
    _choose_preview,
    _client_data,
    _client_preview,
    _notice_overlaps,
    _official_announcement,
    _presentation,
)
from wuwa_story.events.service import browse_events as _game_events_response

__all__ = [
    "_artwork", "_choose_preview", "_client_data", "_client_preview",
    "_notice_overlaps", "_official_announcement", "_presentation", "_game_events_response",
]

router = APIRouter(tags=["game events"])


@router.get("/events")
async def list_game_events(
    server: str = Query(default="europe", pattern="^(asia|europe|america)$"),
    kind: str | None = Query(
        default=None, pattern="^(banner|limited|recurring|permanent)$"),
    version: str | None = Query(default=None, pattern=r"^\d+\.\d+$"),
    locale: str = Query(default="en", pattern=r"^[a-zA-Z-]{2,16}$"),
    summary: bool = Query(
        default=False,
        description="Calendar previews only; detail_url loads the complete artwork gallery.",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _game_events_response(server, kind, version, locale, session, summary=summary)


@router.get("/events/{event_id}/detail")
async def game_event_detail(
    event_id: int,
    server: str = Query(default="europe", pattern="^(asia|europe|america)$"),
    occurrence_id: int | None = Query(default=None),
    locale: str = Query(default="en", pattern=r"^[a-zA-Z-]{2,16}$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await _game_events_response(
        server, None, None, locale, session, event_id=event_id, occurrence_id=occurrence_id,
    )
    if not result["events"]:
        raise HTTPException(status_code=404, detail="Event run not found")
    return result["events"][0]
