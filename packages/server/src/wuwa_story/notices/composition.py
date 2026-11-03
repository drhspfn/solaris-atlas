"""Assemble the notice module for HTTP and background consumers."""

from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.repositories.notices import SqlAlchemyNoticeRepository
from wuwa_story.notices.service import NoticeService
from wuwa_story.storage.notice_images import public_notice_image


def build_notice_service(session: AsyncSession) -> NoticeService:
    return NoticeService(SqlAlchemyNoticeRepository(session), public_notice_image)
