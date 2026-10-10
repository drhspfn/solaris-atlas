"""Public, historical in-game event schedule."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.session import get_session

router = APIRouter(tags=["game events"])


@router.get("/events")
async def list_game_events(
    server: str = Query(default="europe", pattern="^(asia|europe|america)$"),
    kind: str | None = Query(default=None, pattern="^(banner|limited|recurring|permanent)$"),
    version: str | None = Query(default=None, pattern=r"^\d+\.\d+$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    statement = (
        select(GameEvent, GameEventOccurrence)
        .outerjoin(
            GameEventOccurrence,
            and_(
                GameEventOccurrence.event_id == GameEvent.id,
                GameEventOccurrence.server == server,
            ),
        )
        .where(GameEvent.source == "Sanma5657/wuwa-wiki-public")
        .order_by(GameEventOccurrence.starts_at.desc().nullslast(), GameEvent.title)
    )
    if kind:
        statement = statement.where(GameEvent.event_kind == kind)
    if version:
        statement = statement.where(
            (GameEventOccurrence.game_version == version)
            | (GameEventOccurrence.game_version.is_(None))
        )
    rows = (await session.execute(statement)).all()
    return {
        "server": server,
        "source": "Sanma5657/wuwa-wiki-public",
        "source_url": "https://github.com/Sanma5657/wuwa-wiki-public",
        "events": [
            {
                "id": event.id,
                "source_id": event.source_id,
                "occurrence_id": occurrence.id if occurrence else None,
                "title": event.title,
                "description": event.description,
                "kind": event.event_kind,
                "game_version": occurrence.game_version if occurrence else None,
                "starts_at": occurrence.starts_at if occurrence else None,
                "ends_at": occurrence.ends_at if occurrence else None,
                "season": occurrence.season if occurrence else None,
                "rewards": (occurrence.rewards if occurrence else None) or event.rewards,
                "banner_path": (occurrence.banner_path if occurrence else None) or event.banner_path,
                "game_path": event.game_path,
                "source_url": event.source_url,
            }
            for event, occurrence in rows
        ],
    }
