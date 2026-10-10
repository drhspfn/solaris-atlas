"""Public, historical in-game event schedule."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.db.session import get_session
from wuwa_story.storage.s3 import S3Storage

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
    banner_paths = {
        path
        for event, occurrence in rows
        if (
            path := (occurrence.banner_path if occurrence else None) or event.banner_path
        ) is not None
    }
    banner_urls: dict[str, str] = {}
    if banner_paths:
        settings = get_settings()
        source_path_matches = [FileReference.source_path == path for path in banner_paths]
        source_path_matches.extend(
            FileReference.source_path.endswith("/" + path) for path in banner_paths
        )
        media = await session.execute(
            select(FileReference.source_path, FileLocation.object_key)
            .join(FileObject, FileObject.id == FileReference.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(
                or_(*source_path_matches),
                FileLocation.available.is_(True),
                FileLocation.is_primary.is_(True),
                FileLocation.backend == "s3",
                FileLocation.bucket == settings.s3_bucket,
                FileObject.mime_type.in_(
                    ["image/png", "image/jpeg", "image/webp", "image/avif"]
                ),
            )
        )
        storage = S3Storage(settings)
        for source_path, object_key in media:
            if source_path is None:
                continue
            for banner_path in banner_paths:
                if source_path == banner_path or source_path.endswith("/" + banner_path):
                    banner_urls.setdefault(banner_path, storage.public_url(object_key))
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
                "banner_url": banner_urls.get(
                    (occurrence.banner_path if occurrence else None) or event.banner_path or ""
                ),
                "game_path": event.game_path,
                "source_url": event.source_url,
            }
            for event, occurrence in rows
        ],
    }
