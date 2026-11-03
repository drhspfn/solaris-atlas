"""Public archive of the game's official Notice feed."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.api.dependencies import get_notice_service
from wuwa_story.db.session import get_session
from wuwa_story.notices.domain import NoticeNotFound, NoticeRevisionNotFound
from wuwa_story.notices.service import NoticeService

router = APIRouter(tags=["notices"])


@router.get("/notices")
async def list_notices(
    category: str | None = Query(None, pattern="^(game|activity|recommend)$"),
    q: str = Query("", max_length=160),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
    event_id: int | None = Query(None, gt=0),
    session: AsyncSession = Depends(get_session),
    service: NoticeService = Depends(get_notice_service),
):
    if read_projections.should_route_to_projection("game_notice", "current"):
        return await read_projections.notices_page(session, category, q, offset, limit, event_id)
    return await service.page(
        category=category,
        q=q,
        offset=offset,
        limit=limit,
        event_id=event_id,
    )


@router.get("/notices/{notice_id}")
async def notice_detail(
    notice_id: str,
    revision: str | None = Query(None, pattern=r"^[a-f0-9]{64}$"),
    session: AsyncSession = Depends(get_session),
    service: NoticeService = Depends(get_notice_service),
):
    if read_projections.should_route_to_projection("game_notice", "current"):
        return await read_projections.notice_detail_payload(session, notice_id, revision)
    try:
        return await service.detail(notice_id, revision)
    except (NoticeNotFound, NoticeRevisionNotFound) as exc:
        raise HTTPException(404, str(exc)) from exc
