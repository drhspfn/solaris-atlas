"""Read event runs without transferring the full client prefab catalog for lists."""

from types import SimpleNamespace

from sqlalchemy import and_, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.ingestion.game_events import CLIENT_SOURCE


async def read_event_runs(
    session: AsyncSession, *, server: str, kind: str | None = None,
    version: str | None = None, locale: str = "en", summary: bool = False,
    event_id: int | None = None, occurrence_id: int | None = None,
):
    statement = (
        select(GameEvent, GameEventOccurrence)
        .outerjoin(
            GameEventOccurrence,
            and_(
                GameEventOccurrence.event_id == GameEvent.id,
                GameEventOccurrence.server == server,
            ),
        )
        .where(GameEvent.source == CLIENT_SOURCE)
        .order_by(GameEventOccurrence.starts_at.desc().nullslast(), GameEvent.title)
    )
    if kind:
        statement = statement.where(GameEvent.event_kind == kind)
    if version:
        statement = statement.where(
            (GameEventOccurrence.game_version == version)
            | (GameEventOccurrence.game_version.is_(None))
        )
    if event_id is not None:
        statement = statement.where(GameEvent.id == event_id)
        statement = statement.where(
            GameEventOccurrence.id == occurrence_id if occurrence_id is not None
            else GameEventOccurrence.id.is_(None)
        )
    if summary:
        # Project JSON in PostgreSQL: joined ORM rows otherwise transfer and decode
        # the complete prefab catalog once per occurrence before we can trim it.
        columns = []
        names = []
        for model in (GameEvent, GameEventOccurrence):
            fields = [column.key for column in model.__table__.columns]
            names.append(fields)
            columns.extend(
                _summary_source(model.source_data, locale) if name == "source_data"
                else getattr(model, name) for name in fields
            )
        projected = (await session.execute(statement.with_only_columns(*columns))).all()
        split = len(names[0])
        rows = [
            (SimpleNamespace(**dict(zip(names[0], row[:split], strict=True))),
             SimpleNamespace(**dict(zip(names[1], row[split:], strict=True)))
             if row[split] is not None else None)
            for row in projected
        ]
    else:
        rows = (await session.execute(statement)).all()
    return rows


# Only these roles participate in the existing calendar preview/background rules.
_PREVIEW_ROLES = (
    "content", "tab_selected", "tab", "featured_portrait", "featured_weapon",
    "featured_card", "content_background", "background",
)


def _summary_source(source, locale):
    def field(*keys):
        value = source
        for key in keys:
            value = value[key]
        return value

    predicate = " || ".join(f'@.role == "{role}"' for role in _PREVIEW_ROLES)
    client = func.jsonb_build_object(
        "download_id", func.coalesce(field("client", "download_id"), func.to_jsonb("")),
        "asset_version", field("client", "asset_version"),
        "hidden", func.coalesce(field("client", "hidden"), func.to_jsonb(False)),
        "convene_type", field("client", "convene_type"),
        "locales", func.jsonb_build_object(
            "en", field("client", "locales", "en"),
            locale, field("client", "locales", locale),
        ),
        "artwork", func.jsonb_path_query_array(source, cast(f"$.client.artwork[*] ? ({predicate})", JSONPATH)),
        "prefab_artwork", func.jsonb_path_query_array(source, cast(f"$.client.prefab_artwork[*] ? ({predicate})", JSONPATH)),
    )
    return func.jsonb_build_object(
        "client", case(((func.jsonb_typeof(field("client")) == "object") & (field("client") != cast({}, JSONB)), client),
                       else_=func.jsonb_build_object()),
        "notice_id", field("notice_id"),
        "notice_revision", field("notice_revision"),
        "notice_presentation", field("notice_presentation"),
        "official_url", field("official_url"),
        type_=JSONB,
    )


async def read_event_media(session: AsyncSession, banner_paths: set[str], bucket: str):
    source_path_matches = [FileReference.source_path.in_(banner_paths)]
    source_path_matches.extend(
        FileReference.source_path.endswith("/" + path) for path in banner_paths if not path.startswith("/Game/")
    )
    return await session.execute(
        select(FileReference.source_path, FileLocation.object_key, FileReference.metadata_json)
        .join(FileObject, FileObject.id == FileReference.file_id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            or_(*source_path_matches),
            FileLocation.available.is_(True),
            FileLocation.is_primary.is_(True),
            FileLocation.backend == "s3",
            FileLocation.bucket == bucket,
            FileObject.mime_type.in_(
                ["image/png", "image/jpeg", "image/webp", "image/avif"]
            ),
        )
    )


async def read_event_announcements(session: AsyncSession) -> list[GameNotice]:
    return list(await session.scalars(select(GameNotice).where(GameNotice.locale == "en")))


async def read_event_notice_snapshots(
    session: AsyncSession, revisions: set[str],
) -> list[GameNoticeRevision]:
    if not revisions:
        return []
    return list(await session.scalars(
        select(GameNoticeRevision).where(GameNoticeRevision.revision.in_(revisions))
    ))


async def read_event_reward_nodes(session: AsyncSession, reward_keys: set[str]) -> list[Node]:
    if not reward_keys:
        return []
    return list(await session.scalars(select(Node).where(Node.canonical_key.in_(reward_keys))))
