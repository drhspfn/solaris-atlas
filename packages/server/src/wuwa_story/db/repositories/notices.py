"""SQLAlchemy adapter for public notice source reads."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.notices.domain import Notice, NoticeEvent, NoticeRevision
from wuwa_story.urls import numeric_id


def _notice(row: GameNotice) -> Notice:
    return Notice(
        id=row.id,
        slug=row.slug,
        source_id=row.source_id,
        locale=row.locale,
        category=row.category,
        title=row.title,
        revision=row.revision,
        content=row.content,
        source_url=row.source_url,
        published_at=row.published_at,
        observed_at=row.observed_at,
        related_event_ids=row.related_event_ids,
    )


class SqlAlchemyNoticeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(
        self, *, category: str | None, q: str, offset: int, limit: int, event_id: int | None
    ) -> list[Notice]:
        query = select(GameNotice).where(GameNotice.locale == "en")
        if category:
            query = query.where(GameNotice.category == category)
        if q.strip():
            query = query.where(GameNotice.title.icontains(q.strip(), autoescape=True))
        if event_id:
            query = query.where(GameNotice.related_event_ids.contains([event_id]))
        rows = await self.session.scalars(
            query.order_by(GameNotice.published_at.desc().nullslast(), GameNotice.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return [_notice(row) for row in rows]

    async def find(self, identity: str) -> Notice | None:
        identifier = numeric_id(identity)
        row = (
            await self.session.get(GameNotice, identifier)
            if identifier is not None
            else await self.session.scalar(select(GameNotice).where(GameNotice.slug == identity))
        )
        return _notice(row) if row is not None else None

    async def revision(self, notice_id: int, revision: str) -> NoticeRevision | None:
        row = await self.session.get(GameNoticeRevision, (notice_id, revision))
        return NoticeRevision(row.revision, row.content, row.observed_at) if row else None

    async def events(self, event_ids: list[int]) -> list[NoticeEvent]:
        if not event_ids:
            return []
        rows = await self.session.scalars(select(GameEvent).where(GameEvent.id.in_(event_ids)))
        return [NoticeEvent(row.id, row.title, row.slug) for row in rows]

    async def revisions(self, notice_id: int) -> list[str]:
        return list(
            await self.session.scalars(
                select(GameNoticeRevision.revision)
                .where(GameNoticeRevision.notice_id == notice_id)
                .order_by(GameNoticeRevision.observed_at.desc())
            )
        )
