"""Bounded, leased refresh work; source builds never hold publication locks."""

import asyncio
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Numeric,
    and_,
    case,
    cast,
    delete,
    exists,
    func,
    literal,
    literal_column,
    or_,
    select,
    text,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.browse import EntityReadRelation
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.models.graph import Node, NodeRevision, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.notices import GameNotice
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.read_refresh import (
    HISTORICAL_READ_PROJECTION_KINDS,
    READ_PROJECTION_KINDS,
    ReadProjectionDirty,
    ReadProjectionScope,
)
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.ingestion.game_events import CLIENT_SOURCE
from wuwa_story.read_models.publication import publish_entry

NODE_KINDS = {
    "character": ("character",), "item": ("item",), "location": ("location", "area"),
    "quest": ("quest",), "speaker": ("speaker",), "cutscene": ("cutscene",),
    "dialogue": ("dialogue_line", "talk_item", "narration", "player_choice", "phone_message"),
}


@dataclass(frozen=True)
class RefreshClaim:
    kind: str
    entity_id: int
    snapshot_key: str
    generation: int
    lease_token: UUID
    attempts: int


@dataclass(frozen=True)
class BackfillEstimate:
    kind: str
    snapshot_key: str
    entities: int
    locales: int
    rows: int
    estimated_bytes: int
    sampled_rows: int
    estimated_row_bytes: int


class BackfillBudgetExceeded(ValueError):
    """The operator must select a smaller scope or explicitly increase its budget."""


def _backfill_limits(max_rows, max_bytes):
    settings = get_settings()
    return (
        settings.read_projection_max_rows if max_rows is None else max_rows,
        settings.read_projection_max_bytes if max_bytes is None else max_bytes,
    )


def check_backfill_budget(estimate, *, max_rows=None, max_bytes=None):
    max_rows, max_bytes = _backfill_limits(max_rows, max_bytes)
    if max_rows < 1 or max_bytes < 1:
        raise ValueError("Backfill budgets must be positive")
    if estimate.rows > max_rows or estimate.estimated_bytes > max_bytes:
        raise BackfillBudgetExceeded(
            f"{estimate.kind}/{estimate.snapshot_key}: {estimate.rows} projected rows, "
            f"{estimate.estimated_bytes} estimated bytes exceed backfill budget "
            f"({max_rows} rows, {max_bytes} bytes)"
        )


def _identity(claim):
    return (
        ReadProjectionDirty.kind == claim.kind,
        ReadProjectionDirty.entity_id == claim.entity_id,
        ReadProjectionDirty.snapshot_key == claim.snapshot_key,
    )


def _lease(claim):
    return (*_identity(claim), ReadProjectionDirty.lease_token == claim.lease_token,
            ReadProjectionDirty.lease_until > func.clock_timestamp())


async def mark_dirty(session, kind, entity_ids, snapshot_key="current", *, debounce_seconds=2):
    """Caller commits this together with source writes; sorted IDs avoid deadlocks."""
    if kind not in READ_PROJECTION_KINDS:
        raise ValueError(f"Unknown projection kind: {kind}")
    if snapshot_key != "current" and kind not in HISTORICAL_READ_PROJECTION_KINDS:
        raise ValueError(f"{kind} has no public historical snapshot selector")
    if not 0 <= debounce_seconds <= 60:
        raise ValueError("debounce_seconds must be between 0 and 60")
    ids = sorted(set(entity_ids))
    if any(identity < 0 for identity in ids):
        raise ValueError("Projection entity IDs must be nonnegative")
    if not ids:
        return
    scope = insert(ReadProjectionScope).values(kind=kind, snapshot_key=snapshot_key)
    created = await session.scalar(scope.on_conflict_do_nothing(
        index_elements=[ReadProjectionScope.kind, ReadProjectionScope.snapshot_key],
    ).returning(ReadProjectionScope.kind))
    if created is None:
        await session.execute(update(ReadProjectionScope).where(
            ReadProjectionScope.kind == kind, ReadProjectionScope.snapshot_key == snapshot_key,
        ).values(generation=ReadProjectionScope.generation + 1, status="pending",
                 updated_at=func.clock_timestamp()))
    available = func.clock_timestamp() + timedelta(seconds=debounce_seconds)
    if created is not None and 0 not in ids:
        await session.execute(insert(ReadProjectionDirty).values(
            kind=kind, entity_id=0, snapshot_key=snapshot_key, available_at=available,
        ).on_conflict_do_nothing())
    statement = insert(ReadProjectionDirty).values([
        {"kind": kind, "entity_id": identity, "snapshot_key": snapshot_key,
         "available_at": available}
        for identity in ids
    ])
    await session.execute(statement.on_conflict_do_update(
        index_elements=[ReadProjectionDirty.kind, ReadProjectionDirty.entity_id,
                        ReadProjectionDirty.snapshot_key],
        set_={"requested_generation": ReadProjectionDirty.requested_generation + 1,
              "available_at": available},
    ))


async def _lock_scope(session, claim):
    # Enqueuers and publishers take this lock before any dirty-row lock.
    scope = await session.scalar(select(ReadProjectionScope).where(
        ReadProjectionScope.kind == claim.kind,
        ReadProjectionScope.snapshot_key == claim.snapshot_key,
    ).with_for_update())
    if scope is None:
        raise ValueError("Missing projection scope; enqueue through mark_dirty")
    return scope


async def _finish_scope(session, scope):
    await session.flush()
    initialized = exists(select(ReadProjectionDirty.kind).where(
        ReadProjectionDirty.kind == scope.kind,
        ReadProjectionDirty.snapshot_key == scope.snapshot_key,
        ReadProjectionDirty.entity_id == 0,
        ReadProjectionDirty.processed_generation == ReadProjectionDirty.requested_generation,
    ))
    pending = exists(select(ReadProjectionDirty.kind).where(
        ReadProjectionDirty.kind == scope.kind,
        ReadProjectionDirty.snapshot_key == scope.snapshot_key,
        ReadProjectionDirty.processed_generation < ReadProjectionDirty.requested_generation,
    ))
    ready = await session.scalar(select(and_(initialized, ~pending)))
    scope.status = "ready" if ready else "pending"
    if ready:
        scope.published_generation = scope.generation
        scope.published_at = func.clock_timestamp()
        # Empty bulk coverage has no entry write to fire a cache trigger. Bump
        # this kind's revision only when publishing, not a global scope-table row
        # on every source invalidation.
        await session.execute(text(
            "UPDATE ops.public_cache_revision SET transaction_id=txid_current() "
            "WHERE table_name=:name AND transaction_id<>txid_current()"
        ), {"name": f"core.{scope.kind}_read"})
    scope.updated_at = func.clock_timestamp()


def _active_snapshot_import():
    return exists(select(ImportRun.id).join(GameRelease, GameRelease.id == ImportRun.release_id).where(
        ImportRun.importer == "wuwa-deterministic-compiler",
        ImportRun.status == "running",
        or_(
            GameRelease.game_version == ReadProjectionDirty.snapshot_key,
            and_(ReadProjectionDirty.snapshot_key == "current",
                 ReadProjectionDirty.kind.not_in(["game_event", "game_notice"])),
        ),
        ImportRun.id == select(func.max(ImportRun.id)).where(
            ImportRun.importer == "wuwa-deterministic-compiler", ImportRun.status == "running",
        ).correlate(None).scalar_subquery(),
        # Status rows alone cannot prove a live importer. This session lease is
        # held by ordered_import across batch commits and disappears on a crash.
        exists(select(literal_column("1")).select_from(text("pg_locks")).where(text(
            "locktype = 'advisory' AND classid = 527631 AND objid = 1 "
            "AND objsubid = 2 AND granted "
            "AND database=(SELECT oid FROM pg_database WHERE datname=current_database())"
        ))),
    ))


async def claim_refreshes(
    factory, *, limit=10, lease_seconds=60, kinds=None, entity_ids=None, snapshot_key=None,
):
    """Expired leases are reclaimable; each successful claim is committed here."""
    if not 1 <= limit <= 100 or lease_seconds < 3:
        raise ValueError("Claims require limit 1..100 and lease_seconds >= 3")
    if kinds is not None and not set(kinds) <= set(READ_PROJECTION_KINDS):
        raise ValueError("Unknown projection kind")
    if entity_ids is not None:
        entity_ids = tuple(entity_ids)
        if not 1 <= len(entity_ids) <= 100 or any(
            not isinstance(identity, int) or isinstance(identity, bool)
            or identity < 1 or identity > 9223372036854775807 for identity in entity_ids
        ):
            raise ValueError("Entity filters require 1..100 positive PostgreSQL bigint identities")
        entity_ids = tuple(sorted(set(entity_ids)))
    if snapshot_key is not None and (
        not isinstance(snapshot_key, str) or not snapshot_key.strip() or len(snapshot_key) > 256
    ):
        raise ValueError("Snapshot filters require a nonempty key of at most 256 characters")
    async with factory() as session, session.begin():
        statement = select(ReadProjectionDirty).where(
            ReadProjectionDirty.requested_generation > ReadProjectionDirty.processed_generation,
            ReadProjectionDirty.available_at <= func.clock_timestamp(),
            or_(ReadProjectionDirty.lease_until.is_(None),
                ReadProjectionDirty.lease_until <= func.clock_timestamp()),
            ~_active_snapshot_import(),
        ).order_by(ReadProjectionDirty.available_at, ReadProjectionDirty.kind,
                   ReadProjectionDirty.entity_id, ReadProjectionDirty.snapshot_key).limit(limit).with_for_update(skip_locked=True)
        if kinds is not None:
            statement = statement.where(ReadProjectionDirty.kind.in_(kinds))
        if entity_ids is not None:
            statement = statement.where(ReadProjectionDirty.entity_id.in_(entity_ids))
        if snapshot_key is not None:
            statement = statement.where(ReadProjectionDirty.snapshot_key == snapshot_key)
        rows = list(await session.scalars(statement))
        now = await session.scalar(select(func.clock_timestamp())) if rows else None
        claims = []
        for row in rows:
            row.lease_token = uuid4()
            row.lease_until = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            claims.append(RefreshClaim(row.kind, row.entity_id, row.snapshot_key,
                                       row.requested_generation, row.lease_token, row.attempts))
    return claims


async def renew_lease(factory, claim, *, lease_seconds=60):
    if lease_seconds < 3:
        raise ValueError("lease_seconds must be >= 3")
    async with factory() as session, session.begin():
        result = await session.execute(update(ReadProjectionDirty).where(*_lease(claim)).values(
            lease_until=func.clock_timestamp() + timedelta(seconds=lease_seconds),
        ).returning(ReadProjectionDirty.kind))
        return result.scalar_one_or_none() is not None


async def release_claim(factory, claim):
    """Release stale work without acknowledging any generation."""
    async with factory() as session, session.begin():
        await session.execute(update(ReadProjectionDirty).where(
            *_identity(claim), ReadProjectionDirty.lease_token == claim.lease_token,
        ).values(lease_token=None, lease_until=None))


async def fail_claim(factory, claim, error):
    """Persist a safe error class and capped exponential retry; never log payloads."""
    delay = min(300, 2 ** min(claim.attempts, 8))
    async with factory() as session, session.begin():
        await session.execute(update(ReadProjectionDirty).where(
            *_identity(claim), ReadProjectionDirty.lease_token == claim.lease_token,
        ).values(
            lease_token=None, lease_until=None,
            last_error=case((ReadProjectionDirty.requested_generation == claim.generation,
                             str(error) if isinstance(error, BackfillBudgetExceeded)
                             else type(error).__name__), else_=None),
            available_at=case((ReadProjectionDirty.requested_generation == claim.generation,
                               func.clock_timestamp() + timedelta(seconds=delay)),
                              else_=ReadProjectionDirty.available_at),
            attempts=case((ReadProjectionDirty.requested_generation == claim.generation,
                           ReadProjectionDirty.attempts), else_=0),
        ))


async def publish_refresh(factory, claim, entries, relations=()):
    """Fence all locales, deletion, relations and acknowledgement in one commit."""
    if claim.entity_id == 0:
        raise ValueError("Bulk scopes must expand to entity scopes before publication")
    entries = list(entries)
    required = {"entity_id", "locale_id", "snapshot_key", "title", "sort_title", "card", "searchable_text", "payload"}
    if any(not required <= entry.keys() for entry in entries):
        raise ValueError("Incomplete public read projection")
    locales = [entry["locale_id"] for entry in entries]
    if len(locales) != len(set(locales)):
        raise ValueError("Duplicate locale in projection build")
    for entry in entries:
        if entry["entity_id"] != claim.entity_id or entry["snapshot_key"] != claim.snapshot_key:
            raise ValueError("Projection build does not match its dirty scope")
    # Materialize and validate once before any locale writes. The publication
    # function replaces this shared relation set only on the final locale.
    merged = {}
    for relation in relations:
        if not {"related_kind", "related_id", "relation_type"} <= relation.keys():
            raise ValueError("Incomplete projected relationship")
        if relation["related_kind"] not in ENTITY_READ_MODELS:
            raise ValueError("Unknown related entity kind")
        key = (relation["related_kind"], relation["related_id"], relation["relation_type"])
        if key in merged and merged[key] != relation:
            raise ValueError("Conflicting evidence for one projected relationship")
        merged[key] = relation
    relations = list(merged.values())
    async with factory() as session, session.begin():
        scope = await _lock_scope(session, claim)
        row = await session.scalar(select(ReadProjectionDirty).where(
            *_lease(claim), ReadProjectionDirty.requested_generation == claim.generation,
            ReadProjectionDirty.processed_generation < claim.generation,
            ~_active_snapshot_import(),
        ).with_for_update())
        if row is None:
            return False
        model = ENTITY_READ_MODELS[claim.kind]
        # A generation belongs to this exact entity/snapshot scope, never a bulk scope.
        newer = await session.scalar(select(model.entity_id).where(
            model.entity_id == claim.entity_id, model.snapshot_key == claim.snapshot_key,
            model.generation > claim.generation,
        ).limit(1))
        if newer is not None:
            raise ValueError("Projection is newer than its dirty scope")
        for index, entry in enumerate(entries):
            accepted = await publish_entry(
                session, claim.kind, {**entry, "generation": claim.generation},
                relations if index == len(entries) - 1 else None,
            )
            if not accepted:
                raise ValueError("Projection publication rejected an older generation")
        await session.execute(delete(model).where(
            model.entity_id == claim.entity_id, model.snapshot_key == claim.snapshot_key,
            model.locale_id.not_in(locales), model.generation <= claim.generation,
        ))
        if not entries:
            await session.execute(delete(EntityReadRelation).where(
                EntityReadRelation.owner_kind == claim.kind,
                EntityReadRelation.owner_id == claim.entity_id,
                EntityReadRelation.snapshot_key == claim.snapshot_key,
            ))
        row.processed_generation = claim.generation
        row.lease_token = row.lease_until = None
        row.last_error = None
        row.attempts = 0
        await _finish_scope(session, scope)
    return True


def _event_patch(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)*", value):
        return None
    return tuple(int(part) for part in value.split(".")[:2])


def _event_snapshot_ids(snapshot_key):
    patch = _event_patch(snapshot_key)
    if patch is None:
        return select(GameEventOccurrence.event_id).where(False)
    major, minor = patch
    valid = GameEventOccurrence.game_version.op("~")(r"^[0-9]+\.[0-9]+(\.[0-9]+)*$")
    return select(GameEventOccurrence.event_id).where(
        case((valid, cast(func.split_part(GameEventOccurrence.game_version, ".", 1), Numeric))) == major,
        case((valid, cast(func.split_part(GameEventOccurrence.game_version, ".", 2), Numeric))) == minor,
    )


def _source_ids(kind, snapshot_key="current"):
    if kind == "story_map":
        quest_type = case((Quest.quest_type.op("~")("^[0-9]+$"), cast(Quest.quest_type, Numeric)))
        return union(select(literal(1).label("entity_id")), select(
            cast(quest_type + 1, BigInteger).label("entity_id"),
        ).where(quest_type >= 0, quest_type <= 2**63 - 2))
    if kind == "game_event":
        statement = select(GameEvent.id.label("entity_id")).where(GameEvent.source == CLIENT_SOURCE)
        if snapshot_key.startswith("download:"):
            download = snapshot_key.removeprefix("download:")
            statement = statement.where(or_(
                GameEvent.source_data["client"]["download_id"].as_string() == download,
                GameEvent.id.in_(select(GameEventOccurrence.event_id).where(
                    GameEventOccurrence.source_data["client"]["download_id"].as_string() == download,
                )),
            ))
        elif snapshot_key != "current":
            statement = statement.where(GameEvent.id.in_(_event_snapshot_ids(snapshot_key)))
        return statement
    if kind == "game_notice":
        return select(GameNotice.id.label("entity_id"))
    statement = select(Node.id.label("entity_id")).join(NodeType, NodeType.id == Node.type_id).where(
        NodeType.key.in_(NODE_KINDS[kind]),
        Node.status == "active",
    )
    if snapshot_key != "current":
        statement = statement.where(Node.id.in_(select(NodeRevision.node_id).join(
            GameRelease, GameRelease.id == NodeRevision.release_id,
        ).where(GameRelease.game_version == snapshot_key)))
    return statement


async def _source_exists(session, claim, *, snapshot_key=None):
    identities = _source_ids(claim.kind, snapshot_key or claim.snapshot_key).subquery()
    return bool(await session.scalar(select(exists(select(identities.c.entity_id).where(
        identities.c.entity_id == claim.entity_id,
    )))))


async def _deletion_proven(session, claim):
    if await _source_exists(session, claim):
        return False
    if claim.snapshot_key != "current" and await _source_exists(session, claim, snapshot_key="current"):
        # Losing import metadata is not proof of a deletion within that snapshot.
        known = exists(select(GameRelease.id).join(ImportRun, ImportRun.release_id == GameRelease.id).where(
            GameRelease.game_version == claim.snapshot_key, ImportRun.status == "succeeded",
        ))
        if claim.kind == "game_event":
            known = or_(known, exists(_event_snapshot_ids(claim.snapshot_key)))
        if not await session.scalar(select(known)):
            return False
    return True


async def _estimate_backfill(session, kind, snapshot_key):
    if kind not in READ_PROJECTION_KINDS:
        raise ValueError(f"Unknown projection kind: {kind}")
    if snapshot_key != "current" and kind not in HISTORICAL_READ_PROJECTION_KINDS:
        raise ValueError(f"{kind} has no public historical snapshot selector")
    model = ENTITY_READ_MODELS[kind]
    identities = union(_source_ids(kind, snapshot_key), select(model.entity_id).where(
        model.snapshot_key == snapshot_key,
    )).subquery()
    entities = await session.scalar(select(func.count()).select_from(identities))
    locales = 1 if kind == "game_notice" else await session.scalar(select(func.count(Locale.id)))
    # Sample stored attributes without extracting/decompressing full JSON payloads.
    # Include tuple/key overhead, and reserve 2x for indexes, TOAST and replacement.
    size = 256 + sum(func.coalesce(func.pg_column_size(column), 0) for column in (
        model.payload, model.card, model.artwork, model.searchable_text, model.search_vector,
    ))
    sample = select(size.label("bytes")).where(
        model.snapshot_key.in_([snapshot_key, "current"]),
    ).order_by(model.entity_id, model.locale_id).limit(100).subquery()
    count, maximum = (await session.execute(select(func.count(), func.max(sample.c.bytes)))).one()
    row_bytes = max(4096, maximum) if count else 64 * 1024
    rows = entities * locales
    if snapshot_key.startswith("download:"):
        selected = await session.scalar(select(func.count()).select_from(ReadProjectionScope).where(
            ReadProjectionScope.kind == kind,
            ReadProjectionScope.snapshot_key != "current",
            ~ReadProjectionScope.snapshot_key.startswith("download:"),
        ))
        rows *= 1 + selected
    return BackfillEstimate(kind, snapshot_key, entities, locales, rows,
                            rows * row_bytes * 2, count, row_bytes)


async def estimate_backfill(factory, kind, snapshot_key="current"):
    """Read-only CLI preflight. Unsampled payloads reserve 64 KiB per locale row.

    This is a conservative estimate, not a filesystem quota. The caller may sum
    selected scopes before enqueueing and must budget database headroom separately.
    """
    async with factory() as session:
        return await _estimate_backfill(session, kind, snapshot_key)


async def expand_bulk(
    factory, claim, *, batch_size=200, lease_seconds=60,
    max_rows=None, max_bytes=None,
):
    """Keyset-page source AND published IDs, so deleted entities get tombstones.

    A failed expansion restarts from zero. Repeated pages merely advance entity
    dirty generations; partial expansion is durable and cannot lose invalidations.
    """
    if claim.entity_id != 0 or not 1 <= batch_size <= 1000:
        raise ValueError("Expansion requires a bulk claim and batch_size 1..1000")
    model = ENTITY_READ_MODELS[claim.kind]
    max_rows, max_bytes = _backfill_limits(max_rows, max_bytes)
    if claim.snapshot_key.startswith("download:") and claim.kind != "game_event":
        raise ValueError("Download scopes are supported only for game events")
    identities = union(_source_ids(claim.kind, claim.snapshot_key), select(model.entity_id).where(
        model.snapshot_key == claim.snapshot_key,
    )).subquery()
    after = 0
    async with factory() as session:
        if claim.snapshot_key != "current" and not claim.snapshot_key.startswith("download:"):
            known = or_(
                exists(select(GameRelease.id).join(ImportRun, ImportRun.release_id == GameRelease.id).where(
                    GameRelease.game_version == claim.snapshot_key, ImportRun.status == "succeeded",
                )),
                exists(select(model.entity_id).where(model.snapshot_key == claim.snapshot_key)),
                exists(select(ReadProjectionScope.kind).where(
                    ReadProjectionScope.kind == claim.kind,
                    ReadProjectionScope.snapshot_key == claim.snapshot_key,
                    ReadProjectionScope.published_generation > 0,
                )),
            )
            if claim.kind == "game_event":
                known = or_(known, exists(_event_snapshot_ids(claim.snapshot_key)))
            if not await session.scalar(select(known)):
                raise ValueError(f"Snapshot not imported: {claim.snapshot_key}")
        estimate = await _estimate_backfill(session, claim.kind, claim.snapshot_key)
        needs_sample = (estimate.sampled_rows == 0 and 0 < estimate.rows <= max_rows
                        and estimate.estimated_bytes > max_bytes and max_bytes >= 1024**2
                        and not claim.snapshot_key.startswith("download:"))
        if not needs_sample:
            check_backfill_budget(estimate, max_rows=max_rows, max_bytes=max_bytes)
        if needs_sample:
            # A bounded bootstrap is the only exception to the unsampled estimate.
            # It cannot acknowledge coverage or enqueue the rest of the corpus.
            async with factory() as writer, writer.begin():
                scope = await _lock_scope(writer, claim)
                row = await writer.scalar(select(ReadProjectionDirty).where(
                    *_lease(claim), ReadProjectionDirty.requested_generation == claim.generation,
                    ~_active_snapshot_import(),
                ).with_for_update())
                if row is None:
                    return False
                count = max(1, min(3, 64 // max(estimate.locales, 1)))
                ids = list(await writer.scalars(select(identities.c.entity_id).order_by(
                    identities.c.entity_id,
                ).limit(count)))
                await mark_dirty(writer, claim.kind, ids, claim.snapshot_key, debounce_seconds=0)
                row.lease_token = row.lease_until = None
                row.available_at = func.clock_timestamp() + timedelta(seconds=30)
                row.last_error = "Awaiting bounded payload sample"
                scope.status = "building"
            return False
        upper = await session.scalar(select(func.max(identities.c.entity_id))) or 0
    while True:
        async with factory() as session, session.begin():
            scope = await _lock_scope(session, claim)
            row = await session.scalar(select(ReadProjectionDirty).where(
                *_lease(claim), ReadProjectionDirty.requested_generation == claim.generation,
                ~_active_snapshot_import(),
            ).with_for_update())
            if row is None:
                return False
            ids = list(await session.scalars(select(identities.c.entity_id).where(
                identities.c.entity_id > after,
                identities.c.entity_id <= upper,
            ).order_by(identities.c.entity_id).limit(batch_size)))
            if not ids:
                row.processed_generation = claim.generation
                row.lease_token = row.lease_until = None
                row.last_error = None
                row.attempts = 0
                await _finish_scope(session, scope)
                return True
            if claim.snapshot_key.startswith("download:"):
                await mark_dirty(session, claim.kind, ids, debounce_seconds=0)
                versions = await session.execute(select(
                    GameEventOccurrence.event_id, GameEventOccurrence.game_version,
                ).where(
                    GameEventOccurrence.event_id.in_(ids),
                    GameEventOccurrence.game_version.is_not(None),
                    GameEventOccurrence.source_data["client"]["download_id"].as_string()
                    == claim.snapshot_key.removeprefix("download:"),
                ).distinct())
                selected = list(await session.scalars(select(ReadProjectionScope.snapshot_key).where(
                    ReadProjectionScope.kind == claim.kind,
                    ReadProjectionScope.snapshot_key != "current",
                    ~ReadProjectionScope.snapshot_key.startswith("download:"),
                ).order_by(ReadProjectionScope.snapshot_key)))
                targets = {}
                for identity, version in versions:
                    patch = _event_patch(version)
                    if patch is None:
                        continue
                    for snapshot in selected:
                        if _event_patch(snapshot) == patch:
                            targets.setdefault(snapshot, set()).add(identity)
                for snapshot, owners in sorted(targets.items()):
                    await mark_dirty(session, claim.kind, owners, snapshot, debounce_seconds=0)
            else:
                await mark_dirty(session, claim.kind, ids, claim.snapshot_key, debounce_seconds=0)
            scope.status = "building"
            row.lease_until = await session.scalar(select(
                func.clock_timestamp() + timedelta(seconds=lease_seconds),
            ))
            after = ids[-1]


async def refresh_claim(
    factory, claim, *, builder=None, relation_builder=None, lease_seconds=60, locale_batch_size=20,
    max_rows=None, max_bytes=None,
):
    """Build each entity's locales, with an independent heartbeat and fenced publish."""
    if not 1 <= locale_batch_size <= 100:
        raise ValueError("locale_batch_size must be 1..100")
    if builder is None:
        from wuwa_story.read_models.builders import build_entity
        builder = build_entity
    if relation_builder is None:
        from wuwa_story.read_models.builders import build_relations
        relation_builder = build_relations

    async def heartbeat():
        while True:
            await asyncio.sleep(lease_seconds / 3)
            if not await renew_lease(factory, claim, lease_seconds=lease_seconds):
                return

    pulse = asyncio.create_task(heartbeat())
    try:
        if claim.entity_id == 0:
            accepted = await expand_bulk(
                factory, claim, lease_seconds=lease_seconds, max_rows=max_rows, max_bytes=max_bytes,
            )
        else:
            entries = []
            after = 0
            while True:
                async with factory() as session:
                    statement = select(Locale).where(Locale.id > after).order_by(Locale.id).limit(locale_batch_size)
                    if claim.kind == "game_notice":
                        statement = statement.where(Locale.code == select(GameNotice.locale).where(
                            GameNotice.id == claim.entity_id,
                        ).scalar_subquery())
                    locales = list(await session.scalars(statement))
                    if not locales:
                        break
                    for locale in locales:
                        try:
                            entry = await builder(session, claim.kind, claim.entity_id, locale.id, claim.snapshot_key)
                        except Exception as error:
                            if getattr(error, "status_code", None) != 404:
                                raise
                            # A nested archive/source 404 must preserve the last
                            # good publication while the entity still exists.
                            if not await _deletion_proven(session, claim):
                                raise
                            entry = None
                        if entry is not None:
                            entries.append(entry)
                    after = locales[-1].id
            if not entries:
                async with factory() as session:
                    if not await _deletion_proven(session, claim):
                        raise ValueError("Source entity exists or its snapshot is unavailable; empty build rejected")
            relations = ()
            if relation_builder is not None:
                async with factory() as session:
                    relations = await relation_builder(session, claim.kind, claim.entity_id, claim.snapshot_key)
            if pulse.done():
                pulse.result()
            accepted = await publish_refresh(factory, claim, entries, relations)
        if not accepted:
            await release_claim(factory, claim)
        return accepted
    except asyncio.CancelledError:
        # Crash/cancellation leaves a recoverable expiring lease.
        raise
    except Exception as error:
        await fail_claim(factory, claim, error)
        raise
    finally:
        pulse.cancel()
        # Heartbeat cleanup must not replace a runtime cancellation with a
        # concurrent database error and hide the worker's TimeoutError handling.
        await asyncio.gather(pulse, return_exceptions=True)
