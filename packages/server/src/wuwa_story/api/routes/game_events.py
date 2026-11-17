"""Public, historical in-game event schedule."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.api.dependencies import get_event_service
from wuwa_story.db.models.game_events import GameEventSnapshot
from wuwa_story.db.session import get_session
from wuwa_story.events.composition import browse_events as _game_events_response
from wuwa_story.events.domain import EventRunNotFound

# Preserve helper imports used by existing event/notice consumers and tests.
from wuwa_story.events.service import (
    EventService,
    _artwork,
    _choose_preview,
    _client_data,
    _client_preview,
    _notice_overlaps,
    _official_announcement,
    _presentation,
)

__all__ = [
    "_artwork",
    "_choose_preview",
    "_client_data",
    "_client_preview",
    "_notice_overlaps",
    "_official_announcement",
    "_presentation",
    "_game_events_response",
]

router = APIRouter(tags=["game events"])


@router.get("/events")
async def list_game_events(
    server: str = Query(default="europe", pattern="^(asia|europe|america)$"),
    kind: str | None = Query(
        default=None, pattern="^(banner|limited|recurring|permanent)$"),
    version: str | None = Query(default=None, pattern=r"^\d+\.\d+(?:\.\d+)?$"),
    locale: str = Query(default="en", pattern=r"^[a-zA-Z-]{2,16}$"),
    summary: bool = Query(
        default=False,
        description="Calendar previews only; detail_url loads the complete artwork gallery.",
    ),
    session: AsyncSession = Depends(get_session),
    service: EventService = Depends(get_event_service),
) -> dict[str, Any]:
    if version:
        if version.count(".") == 1:
            candidates = list(await session.scalars(select(GameEventSnapshot.version).where(
                GameEventSnapshot.version.startswith(version + ".", autoescape=True),
                GameEventSnapshot.complete.is_(True),
            )))
            if len(candidates) > 1:
                raise HTTPException(status_code=409, detail="Multiple event snapshots match; specify the full version")
            if not candidates:
                raise HTTPException(status_code=404, detail="Event catalog snapshot not available")
            version = candidates[0]
        snapshot = await session.get(GameEventSnapshot, version)
        if snapshot is None or not snapshot.complete:
            raise HTTPException(status_code=404, detail="Event catalog snapshot not available")
        if read_projections.should_route_to_projection("game_event", None, locale=locale):
            response = await read_projections.events_page(session, server, kind, version, locale, summary)
        else:
            response = await service.browse(server, kind, version, locale, summary=summary)
        return {**response, "snapshot_presentation": "versioned_catalog_capture"}
    if read_projections.should_route_to_projection("game_event", None, locale=locale):
        return await read_projections.events_page(session, server, kind, version, locale, summary)
    return await service.browse(server, kind, version, locale, summary=summary)


@router.get("/events/snapshots")
async def list_event_snapshots(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    versions = list(await session.scalars(select(GameEventSnapshot.version).where(GameEventSnapshot.complete.is_(True))))
    return {"versions": sorted(versions, key=lambda value: tuple(map(int, value.split("."))), reverse=True)}


@router.get("/events/{event_id}/detail")
async def game_event_detail(
    event_id: int,
    server: str = Query(default="europe", pattern="^(asia|europe|america)$"),
    occurrence_id: int | None = Query(default=None),
    locale: str = Query(default="en", pattern=r"^[a-zA-Z-]{2,16}$"),
    version: str | None = Query(default=None, pattern=r"^\d+\.\d+\.\d+$"),
    session: AsyncSession = Depends(get_session),
    service: EventService = Depends(get_event_service),
) -> dict[str, Any]:
    if version:
        snapshot = await session.get(GameEventSnapshot, version)
        if snapshot is None or not snapshot.complete:
            raise HTTPException(404, "Event catalog snapshot not available")
    if read_projections.should_route_to_projection("game_event", version or "current", locale=locale):
        return await read_projections.event_detail(session, event_id, server, occurrence_id, locale, version) if version else await read_projections.event_detail(
            session, event_id, server, occurrence_id, locale)
    try:
        return await service.detail(event_id, server, occurrence_id, locale, version=version) if version else await service.detail(
            event_id, server, occurrence_id, locale)
    except EventRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
