"""Read event runs without transferring the full client prefab catalog for lists."""

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Text, and_, case, cast, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.events.catalog import is_catalog_capture
from wuwa_story.events.catalog_duplicates import client_signature, visible_occurrences
from wuwa_story.events.domain import (
    CLIENT_SOURCE,
    PREFAB_PREVIEWS,
    Event,
    EventAnnouncement,
    EventMedia,
    EventNoticeSnapshot,
    EventOccurrence,
    EventRewardNode,
    EventRun,
)
from wuwa_story.events.domain import PREVIEW_ROLES as _PREVIEW_ROLES
from wuwa_story.events.repository import EVENT_RUN_BATCH_SIZE
from wuwa_story.events.selectable_pools import proven_member_ids


def _capture_enrichment(capture, candidate):
    same_source = (candidate["download_id"].as_string() == capture["download_id"].as_string()) & (
        candidate["asset_version"].as_string() == capture["asset_version"].as_string())
    for field in ("resources", "artwork"):
        same_source &= candidate[field].is_not_distinct_from(capture[field])
    extra = func.jsonb_strip_nulls(func.jsonb_build_object(*[
        value for field in ("prefab_artwork", "prefab_spine_resources", "missing_resources", "animation_import")
        for value in (field, candidate[field])
    ]), type_=JSONB)
    return case((same_source, extra), else_=cast({}, JSONB))


def _captured_source(source, captured, *, event_source=None):
    client = captured.op("||")(_capture_enrichment(captured, source["client"]))
    if event_source is not None:
        client = client.op("||")(_capture_enrichment(captured, event_source["client"]))
    return func.jsonb_set(func.coalesce(source, cast({}, JSONB)),
        cast(literal("{client}"), ARRAY(Text)), client, type_=JSONB)


async def _read_occurrence_batch(session, *, server, version, event_id, occurrence_ids):
    """Read the large event header once; transfer at most 16 period captures at a time."""
    if event_id is None or occurrence_ids is not None and (
        not occurrence_ids or len(occurrence_ids) > EVENT_RUN_BATCH_SIZE
    ):
        raise ValueError("Event occurrence batches require one event and 1..16 period IDs")
    from wuwa_story.read_models.context import SOURCE_BUILD

    cache = session.info.setdefault("story_event_batch_headers", {}) if SOURCE_BUILD.get() else {}
    key = (event_id, version)
    if key not in cache:
        event = await session.get(GameEvent, event_id)
        if event is None or event.source != CLIENT_SOURCE:
            return []
        if version:
            # Only the selected client capture crosses this query, not another full header.
            capture = GameEventSnapshotMember.client_capture
            client = await session.scalar(select(
                capture.op("||")(_capture_enrichment(capture, GameEvent.source_data["client"]))
            ).select_from(GameEventSnapshotMember)
                .join(GameEvent, GameEvent.id == GameEventSnapshotMember.event_id)
                .join(GameEventSnapshot, GameEventSnapshot.version == GameEventSnapshotMember.version)
                .where(GameEventSnapshotMember.event_id == event_id,
                       GameEventSnapshotMember.version == version, GameEventSnapshot.complete.is_(True)))
            if client is None:
                return []
            if not isinstance(client, dict) or not client.get("download_id"):
                raise HTTPException(503, "The selected event snapshot capture is not ready")
            event = SimpleNamespace(**{column.key: getattr(event, column.key)
                                       for column in GameEvent.__table__.columns})
            event.source_data = {**(event.source_data or {}), "client": client}
        cache[key] = event
    event = cache[key]
    if occurrence_ids is None:
        existing = await session.scalar(select(GameEventOccurrence.id).where(
            GameEventOccurrence.event_id == event_id, GameEventOccurrence.server == server).limit(1))
        return [(event, None)] if existing is None else []
    fields = [column.key for column in GameEventOccurrence.__table__.columns]
    source = GameEventOccurrence.source_data
    statement = select(GameEventOccurrence).where(
        GameEventOccurrence.event_id == event_id, GameEventOccurrence.server == server,
        GameEventOccurrence.id.in_(occurrence_ids),
    ).order_by(GameEventOccurrence.starts_at.desc().nullslast(), GameEventOccurrence.id)
    if version:
        statement = (statement.join(GameEvent, GameEvent.id == GameEventOccurrence.event_id)
            .join(GameEventSnapshotMember, and_(GameEventSnapshotMember.event_id == GameEvent.id,
                                               GameEventSnapshotMember.version == version))
            .join(GameEventSnapshot, GameEventSnapshot.version == GameEventSnapshotMember.version)
            .where(GameEventSnapshot.complete.is_(True)))
        source = _captured_source(source, GameEventSnapshotMember.client_capture,
                                  event_source=GameEvent.source_data)
    # No joined GameEvent.* columns: its potentially huge prefab JSON is shared.
    rows = (await session.execute(statement.with_only_columns(*[
        source if field == "source_data" else getattr(GameEventOccurrence, field)
        for field in fields
    ]))).all()
    return [(event, SimpleNamespace(**dict(zip(fields, row, strict=True)))) for row in rows]


async def read_event_runs(
    session: AsyncSession, *, server: str, kind: str | None = None,
    version: str | None = None, locale: str = "en", summary: bool = False,
    event_id: int | None = None, occurrence_id: int | None = None,
    occurrence_ids: list[int] | None = None,
) -> list[Any]:
    if occurrence_ids is not None:
        if kind is not None or summary or occurrence_id is not None:
            raise ValueError("Occurrence batching is for full, exact event projection only")
        return await _read_occurrence_batch(session, server=server, version=version,
                                          event_id=event_id, occurrence_ids=occurrence_ids)
    from wuwa_story.read_models.context import SOURCE_BUILD

    if SOURCE_BUILD.get() and server == "" and event_id is not None and occurrence_id is None and not summary and kind is None:
        return await _read_occurrence_batch(session, server=server, version=version,
                                          event_id=event_id, occurrence_ids=None)
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
        statement = statement.join(GameEventSnapshotMember, and_(
            GameEventSnapshotMember.event_id == GameEvent.id, GameEventSnapshotMember.version == version
        )).join(GameEventSnapshot, GameEventSnapshot.version == GameEventSnapshotMember.version).where(
            GameEventSnapshot.complete.is_(True))
    if event_id is not None:
        if occurrence_id is not None:
            root = select(func.coalesce(GameEvent.canonical_event_id, GameEvent.id)).where(
                GameEvent.id == event_id).scalar_subquery()
            statement = statement.where(func.coalesce(GameEvent.canonical_event_id, GameEvent.id) == root)
        else:
            statement = statement.where(GameEvent.id == event_id)
        statement = statement.where(
            GameEventOccurrence.id == occurrence_id if occurrence_id is not None
            else GameEventOccurrence.id.is_(None)
        )
    if summary or version:
        # Project JSON in PostgreSQL: joined ORM rows otherwise transfer and decode
        # the complete prefab catalog once per occurrence before we can trim it.
        columns: list[Any] = []
        names = []
        for model in (GameEvent, GameEventOccurrence):
            fields = [column.key for column in model.__table__.columns]
            names.append(fields)
            source = model.source_data
            if version:
                captured = GameEventSnapshotMember.client_capture
                source = _captured_source(source, captured,
                    event_source=GameEvent.source_data if model is GameEventOccurrence else None)
            columns.extend(
                (_summary_source(source, locale) if summary else source) if name == "source_data"
                else getattr(model, name) for name in fields)
        projected = (await session.execute(statement.with_only_columns(*columns))).all()
        split = len(names[0])
        rows: list[Any] = [
            (SimpleNamespace(**dict(zip(names[0], row[:split], strict=True))),
             SimpleNamespace(**dict(zip(names[1], row[split:], strict=True)))
             if row[split] is not None else None)
            for row in projected
        ]
    else:
        rows = list((await session.execute(statement)).all())
    if version and any(not isinstance(event.source_data, dict) or not isinstance(
        event.source_data.get("client"), dict) or not event.source_data["client"].get("download_id")
        for event, _ in rows):
        raise HTTPException(503, "The selected event snapshot capture is not ready")
    if event_id is None:
        placeholders = {
            occurrence.id for _, occurrence in rows
            if occurrence and is_catalog_capture(occurrence.source_occurrence_id,
                occurrence.starts_at, occurrence.ends_at, occurrence.source_data)
        }
        keep = visible_occurrences(
            [(event.id, occurrence.id if occurrence else None,
              occurrence.game_version if occurrence else None,
              client_signature(occurrence.source_data) if occurrence else None,
              getattr(event, "event_kind", None))
             for event, occurrence in rows],
            placeholders,
        )
        rows = [(event, occurrence) for event, occurrence in rows
                if not occurrence or occurrence.id not in placeholders or occurrence.id in keep]
    return rows


def _summary_source(source: Any, locale: str) -> Any:
    def field(*keys: str) -> Any:
        value = source
        for key in keys:
            value = value[key]
        return value

    predicate = " || ".join(f'@.role == "{role}"' for role in _PREVIEW_ROLES)
    prefab_predicate = " || ".join(
        f'(@.role == "prefab_texture" && @.prefab == "{prefab}" && @.path == "{path}")'
        for prefab, path in PREFAB_PREVIEWS.values()
    )
    client = func.jsonb_build_object(
        "download_id", func.coalesce(field("client", "download_id"), func.to_jsonb("")),
        "asset_version", field("client", "asset_version"),
        "hidden", func.coalesce(field("client", "hidden"), func.to_jsonb(False)),
        "convene_type", field("client", "convene_type"),
        "gacha_type", field("client", "gacha_type"),
        "gacha_pool_proof", field("client", "gacha_pool_proof"),
        "table", field("client", "table"),
        "title_key", field("client", "title_key"),
        "description_key", field("client", "description_key"),
        "featured_ids", field("client", "featured_ids"),
        "animation_import", field("client", "animation_import"),
        "locales", func.jsonb_build_object(
            "en", field("client", "locales", "en"),
            locale, field("client", "locales", locale),
        ),
        "artwork", func.jsonb_path_query_array(source, cast(f"$.client.artwork[*] ? ({predicate})", JSONPATH)),
        "prefab_artwork", func.jsonb_path_query_array(source, cast(f"$.client.prefab_artwork[*] ? ({predicate} || {prefab_predicate})", JSONPATH)),
    )
    return func.jsonb_build_object(
        "client", case(((func.jsonb_typeof(field("client")) == "object") & (field("client") != cast({}, JSONB)), client),
                       else_=func.jsonb_build_object()),
        "notice_id", field("notice_id"),
        "notice_revision", field("notice_revision"),
        "schedule_revision", field("schedule_revision"),
        "notice_match", field("notice_match"),
        "notice_presentation", field("notice_presentation"),
        "start_estimate", field("start_estimate"),
        "schedule_source", field("schedule_source"),
        "manual_schedule", field("manual_schedule"),
        "official_url", field("official_url"),
        type_=JSONB,
    )


async def read_event_media(
    session: AsyncSession, banner_paths: set[str], bucket: str, *,
    captures: set[tuple[str, str]] | None = None,
) -> list[Any]:
    if not banner_paths:
        return []
    from wuwa_story.read_models.context import SOURCE_BUILD

    cache = session.info.setdefault("story_event_media", {"exact": {}, "suffix": {}}) if SOURCE_BUILD.get() else None

    def media_query(path_filter: Any) -> Any:
        # Fence the path lookup so the large media joins see only candidate refs.
        reference_columns = [FileReference.file_id, FileReference.source_path,
                             FileReference.metadata_json.label("metadata_json")]
        if cache is not None:
            reference_columns.append(FileReference.id.label("reference_id"))
        candidates = (
            select(*reference_columns)
            .where(path_filter)
            .cte("event_media_candidates")
            .prefix_with("MATERIALIZED", dialect="postgresql")
        )
        statement = (
            select(candidates.c.source_path, FileLocation.object_key, candidates.c.metadata_json)
            .select_from(candidates)
            .join(FileObject, FileObject.id == candidates.c.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(
                FileLocation.available.is_(True),
                FileLocation.is_primary.is_(True),
                FileLocation.backend == "s3",
                FileLocation.bucket == bucket,
                FileObject.mime_type.in_(
                    ["image/png", "image/jpeg", "image/webp", "image/avif"]
                ),
            )
        )
        # Reference identity prevents overlapping suffixes from duplicating a
        # row when independently cached path results are combined later.
        return statement.add_columns(candidates.c.reference_id) if cache is not None else statement

    async def cached_rows(paths: list[str], *, suffix: bool = False) -> list[Any]:
        if cache is None:
            predicate = or_(*(FileReference.source_path.endswith("/" + path, autoescape=True) for path in paths)) if suffix else FileReference.source_path.in_(paths)
            return list(await session.execute(media_query(predicate)))
        entries = cache["suffix" if suffix else "exact"]
        missing = [path for path in paths if (bucket, path) not in entries]
        if missing:
            predicate = or_(*(FileReference.source_path.endswith("/" + path, autoescape=True) for path in missing)) if suffix else FileReference.source_path.in_(missing)
            fetched = list(await session.execute(media_query(predicate)))
            for path in missing:
                entries[(bucket, path)] = [
                    row for row in fetched
                    if row[0] is not None and (row[0].endswith("/" + path) if suffix else row[0] == path)
                ]
        # A broad suffix already contains every match for nested suffixes. Use
        # its complete fetched order rather than prepending narrower results.
        output_paths = [path for path in paths if not any(
            path != other and path.endswith("/" + other) for other in paths
        )] if suffix else paths
        rows, seen = [], set()
        for path in output_paths:
            for source_path, object_key, metadata, reference_id in entries[(bucket, path)]:
                if reference_id not in seen:
                    rows.append((source_path, object_key, metadata))
                    seen.add(reference_id)
        return rows

    rows = await cached_rows(sorted(banner_paths))
    resolved = {((metadata or {}).get("download_id", ""), path) for path, _, metadata in rows}
    # An exact hit from another capture must not hide an older run's suffix asset.
    unresolved = (
        {path for download, path in captures if (download, path) not in resolved}
        if captures is not None else banner_paths - {path for path, _, _ in rows}
    )
    relative_paths = sorted(path for path in unresolved if not path.startswith("/Game/"))
    if relative_paths:
        rows.extend(await cached_rows(relative_paths, suffix=True))
    return rows


async def read_event_announcements(
    session: AsyncSession, *, event_id: int | None = None, notice_ids: set[int] | None = None,
) -> list[GameNotice]:
    statement = select(GameNotice).where(GameNotice.locale == "en")
    if event_id is not None:
        statement = statement.where(or_(
            GameNotice.related_event_ids.contains([event_id]),
            GameNotice.related_event_ids == [],
            GameNotice.id.in_(notice_ids or set()),
        ))
    return list(await session.scalars(statement))


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


class SqlAlchemyEventRepository:
    """Translate optimized source queries to persistence-free event read models."""

    def __init__(self, session: AsyncSession, bucket: str) -> None:
        self.session = session
        self.bucket = bucket

    async def pool_occurrences(self, *, server, proof, starts_at, ends_at):
        notice = await self.session.get(GameNotice, proof["notice_id"])
        members = proven_member_ids(
            notice.revision if notice else None, notice.related_event_ids if notice else None,
            proof["notice_revision"],
        )
        if not members:
            return []
        data = GameEventOccurrence.source_data
        statement = (
            select(GameEventOccurrence.event_id, GameEventOccurrence.id)
            .join(GameEvent, GameEvent.id == GameEventOccurrence.event_id)
            .where(
                GameEvent.source == CLIENT_SOURCE,
                GameEvent.event_kind == "banner",
                GameEventOccurrence.event_id.in_(members),
                GameEventOccurrence.server == server,
                GameEventOccurrence.starts_at == starts_at,
                GameEventOccurrence.ends_at == ends_at,
                data["notice_id"].as_integer() == proof["notice_id"],
                data["notice_revision"].as_string() == proof["notice_revision"],
                data["notice_match"]["basis"].as_string() == "gacha_pool",
                data["notice_match"]["release_id"].as_integer() == proof["release_id"],
                data["notice_match"]["gacha_id"].as_integer() == proof["gacha_id"],
            )
            .order_by(GameEventOccurrence.event_id, GameEventOccurrence.id)
            .limit(65)
        )
        rows = list((await self.session.execute(statement)).all())
        if len(rows) != len(members) or {event_id for event_id, _ in rows} != set(members):
            return []
        return rows

    async def spine(self, captures: set[tuple[str, str]]) -> list[EventMedia]:
        if not captures:
            return []
        rows = await self.session.execute(
            select(FileReference.source_path, FileLocation.object_key, FileReference.metadata_json,
                   FileObject.id, FileObject.size_bytes)
            .select_from(FileReference)
            .join(FileObject, FileObject.id == FileReference.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(FileReference.reference_type == 'event_spine',
                   tuple_(FileReference.source_path, FileReference.source_name).in_(captures),
                   FileObject.mime_type == 'application/zip',
                   FileLocation.available.is_(True), FileLocation.is_primary.is_(True),
                   FileLocation.backend == 's3', FileLocation.bucket == self.bucket)
            .order_by(FileReference.id.desc())
        )
        return [EventMedia(path, key, metadata or {}, identity, size)
                for path, key, metadata, identity, size in rows]

    async def runs(
        self, *, server: str, kind: str | None, version: str | None, locale: str,
        summary: bool, event_id: int | None, occurrence_id: int | None,
        occurrence_ids: list[int] | None = None,
    ) -> list[EventRun]:
        rows = await read_event_runs(
            self.session, server=server, kind=kind, version=version, locale=locale,
            summary=summary, event_id=event_id, occurrence_id=occurrence_id,
            **({"occurrence_ids": occurrence_ids} if occurrence_ids is not None else {}),
        )
        return [
            EventRun(
                Event(
                    id=event.id, slug=event.slug, source_id=event.source_id,
                    title=event.title, description=event.description, event_kind=event.event_kind,
                    banner_path=event.banner_path, rewards=event.rewards, game_path=event.game_path,
                    source_url=event.source_url, source_data=event.source_data,
                    canonical_event_id=getattr(event, "canonical_event_id", None),
                ),
                EventOccurrence(
                    id=occurrence.id, game_version=occurrence.game_version,
                    starts_at=occurrence.starts_at, ends_at=occurrence.ends_at,
                    season=occurrence.season, banner_path=occurrence.banner_path,
                    source_data=occurrence.source_data,
                    source_occurrence_id=getattr(occurrence, "source_occurrence_id", None),
                ) if occurrence else None,
            )
            for event, occurrence in rows
        ]

    async def announcements(
        self, *, event_id: int | None, notice_ids: set[int],
    ) -> list[EventAnnouncement]:
        rows = await read_event_announcements(self.session, event_id=event_id, notice_ids=notice_ids)
        return [EventAnnouncement(row.id, row.slug, row.title, row.related_event_ids,
                                  row.published_at, row.content) for row in rows]

    async def notice_snapshots(self, revisions: set[str]) -> list[EventNoticeSnapshot]:
        rows = await read_event_notice_snapshots(self.session, revisions)
        return [EventNoticeSnapshot(row.notice_id, row.revision, row.content) for row in rows]

    async def reward_nodes(self, reward_keys: set[str]) -> list[EventRewardNode]:
        rows = await read_event_reward_nodes(self.session, reward_keys)
        return [EventRewardNode(row.canonical_key, row.slug) for row in rows]

    async def media(
        self, paths: set[str], *, captures: set[tuple[str, str]],
    ) -> list[EventMedia]:
        rows = await read_event_media(self.session, paths, self.bucket, captures=captures)
        return [EventMedia(path, key, metadata or {}) for path, key, metadata in rows]
