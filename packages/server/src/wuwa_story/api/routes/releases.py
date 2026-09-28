from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.repositories.releases import list_releases
from wuwa_story.db.session import get_session

router = APIRouter(prefix="/releases", tags=["releases"])


class ReleaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    game_version: str
    resource_version: str | None
    changelist: int | None
    upstream_name: str
    upstream_commit: str | None
    imported_at: datetime


@router.get("", response_model=list[ReleaseResponse])
async def get_releases(
    limit: int = Query(50, ge=1, le=200), session: AsyncSession = Depends(get_session)
) -> list[ReleaseResponse]:
    return [ReleaseResponse.model_validate(row) for row in await list_releases(session, limit)]
