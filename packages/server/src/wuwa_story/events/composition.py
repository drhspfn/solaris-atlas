"""Assemble the event module for HTTP and background projection publication."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.config.settings import get_settings
from wuwa_story.db.repositories.event_browsing import SqlAlchemyEventRepository
from wuwa_story.events.service import EventService
from wuwa_story.storage.notice_images import public_notice_image
from wuwa_story.storage.s3 import S3Storage


def build_event_service(session: AsyncSession) -> EventService:
    settings = get_settings()
    storage = S3Storage(settings)
    return EventService(
        SqlAlchemyEventRepository(session, settings.s3_bucket),
        storage.public_url,
        public_notice_image,
    )


async def browse_events(
    server: str,
    kind: str | None,
    version: str | None,
    locale: str,
    session: AsyncSession,
    *,
    summary: bool = False,
    event_id: int | None = None,
    occurrence_id: int | None = None,
) -> dict[str, Any]:
    """Source renderer shared with background publishers; no HTTP handler calls."""
    return await build_event_service(session).browse(
        server,
        kind,
        version,
        locale,
        summary=summary,
        event_id=event_id,
        occurrence_id=occurrence_id,
    )
