"""Public archive of the game's official Notice feed."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.db.session import get_session
from wuwa_story.ingestion.notices import public_notice_image
from wuwa_story.urls import numeric_id, record_url

router = APIRouter(tags=["notices"])


def notice_summary(notice):
    return {
        "id": notice.id,
        "slug": getattr(notice, "slug", None),
        "href": record_url(notice, "notices"),
        "source_id": notice.source_id,
        "locale": notice.locale,
        "category": notice.category,
        "title": notice.title,
        "revision": notice.revision,
        "banner_url": public_notice_image(notice.content.get("banner_url")),
        "source_url": notice.source_url,
        "available_from": notice.published_at,
        "observed_at": notice.observed_at,
        "related_event_ids": notice.related_event_ids,
    }


@router.get("/notices")
async def list_notices(
    category: str | None = Query(None, pattern="^(game|activity|recommend)$"),
    q: str = Query("", max_length=160),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
    event_id: int | None = Query(None, gt=0),
    session: AsyncSession = Depends(get_session),
):
    if read_projections.should_route_to_projection("game_notice", "current"):
        return await read_projections.notices_page(session, category, q, offset, limit, event_id)
    query = select(GameNotice).where(GameNotice.locale == "en")
    if category:
        query = query.where(GameNotice.category == category)
    if q.strip():
        query = query.where(GameNotice.title.icontains(q.strip(), autoescape=True))
    if event_id:
        query = query.where(GameNotice.related_event_ids.contains([event_id]))
    rows = list(
        await session.scalars(
            query.order_by(GameNotice.published_at.desc().nullslast(), GameNotice.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return {
        "notices": [notice_summary(n) for n in rows[:limit]],
        "has_more": len(rows) > limit,
        "offset": offset,
    }


@router.get("/notices/{notice_id}")
async def notice_detail(
    notice_id: str,
    revision: str | None = Query(None, pattern=r"^[a-f0-9]{64}$"),
    session: AsyncSession = Depends(get_session),
):
    if read_projections.should_route_to_projection("game_notice", "current"):
        return await read_projections.notice_detail_payload(session, notice_id, revision)
    identifier = numeric_id(notice_id)
    notice = (await session.get(GameNotice, identifier) if identifier is not None else
              await session.scalar(select(GameNotice).where(GameNotice.slug == notice_id)))
    if notice is None:
        raise HTTPException(404, "Announcement not found")
    notice_id = notice.id
    content = notice.content
    snapshot = None
    if revision:
        snapshot = await session.get(GameNoticeRevision, (notice_id, revision))
        if snapshot is None:
            raise HTTPException(404, "Announcement revision not found")
        content = snapshot.content
    related_ids = content.get("related_event_ids", notice.related_event_ids)
    events = (
        list(await session.scalars(select(GameEvent).where(GameEvent.id.in_(related_ids))))
        if related_ids
        else []
    )
    revisions = list(
        await session.scalars(
            select(GameNoticeRevision.revision)
            .where(GameNoticeRevision.notice_id == notice.id)
            .order_by(GameNoticeRevision.observed_at.desc())
        )
    )
    return {
        **notice_summary(notice),
        "title": content.get("title", notice.title),
        "category": content.get("category", notice.category),
        "related_event_ids": related_ids,
        "observed_at": snapshot.observed_at if snapshot else notice.observed_at,
        "revision": revision or notice.revision,
        "banner_url": public_notice_image(content.get("banner_url")),
        "blocks": [{**block, "url": public_notice_image(block.get("url"))}
                   if block["type"] == "image" else block for block in content.get("blocks", [])],
        "links": content.get("links", []),
        "revisions": revisions,
        "events": [{"id": e.id, "title": e.title, "slug": getattr(e, "slug", None), "href": record_url(e, "events")} for e in events],
    }
