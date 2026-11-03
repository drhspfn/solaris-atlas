from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.config.settings import get_settings
from wuwa_story.db.session import get_session
from wuwa_story.notices.composition import build_notice_service
from wuwa_story.notices.service import NoticeService

__all__ = ["get_notice_service", "get_session", "get_settings"]


def get_notice_service(session: AsyncSession = Depends(get_session)) -> NoticeService:
    return build_notice_service(session)
