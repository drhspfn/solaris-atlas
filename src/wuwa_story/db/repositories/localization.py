from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.i18n import LocalizationKey, LocalizationValue


async def get_localized_value(
    session: AsyncSession, key: str, locale_id: int, release_id: int
) -> LocalizationValue | None:
    statement = (
        select(LocalizationValue)
        .join(LocalizationKey, LocalizationValue.key_id == LocalizationKey.id)
        .where(
            LocalizationKey.key == key,
            LocalizationValue.locale_id == locale_id,
            LocalizationValue.release_id == release_id,
        )
    )
    return await session.scalar(statement)
