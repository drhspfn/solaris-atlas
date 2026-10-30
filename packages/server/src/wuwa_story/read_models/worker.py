"""Run durable public projection refreshes independently of HTTP requests."""

import argparse
import asyncio
import json
import logging
import re
import shutil
import time
from dataclasses import asdict

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.read_refresh import READ_PROJECTION_KINDS
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.policy import HISTORICAL_KINDS
from wuwa_story.read_models.refresh import (
    claim_refreshes,
    estimate_backfill,
    fail_claim,
    mark_dirty,
    refresh_claim,
)

logger = logging.getLogger(__name__)
PROJECTION_STATEMENT_TIMEOUT_MS = 60_000
PROJECTION_LOCK_TIMEOUT_MS = 1_000
CLAIM_TIMEOUT_SECONDS = 10 * 60


def create_worker_database(
    database_url=None, *, statement_timeout_ms=PROJECTION_STATEMENT_TIMEOUT_MS,
    lock_timeout_ms=PROJECTION_LOCK_TIMEOUT_MS,
):
    """Keep projection limits local to this engine, including pooled transactions."""
    if statement_timeout_ms < 1 or lock_timeout_ms < 1:
        raise ValueError("Worker database timeouts must be positive")
    worker_engine = create_async_engine(
        database_url or get_settings().database_url, pool_pre_ping=True,
        pool_size=3, max_overflow=0, pool_timeout=5,
    )

    @event.listens_for(worker_engine.sync_engine, "begin")
    def bounded_transaction(connection):
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{int(statement_timeout_ms)}ms'")
        connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")

    return worker_engine, async_sessionmaker(worker_engine, expire_on_commit=False, autoflush=False)


engine, SessionFactory = create_worker_database()


async def refresh_batch(
    factory=SessionFactory, *, limit=1, kinds=None, entity_ids=None, snapshot_key=None,
    claim_timeout_seconds=CLAIM_TIMEOUT_SECONDS,
):
    if claim_timeout_seconds <= 0:
        raise ValueError("Claim runtime timeout must be positive")
    claims = await claim_refreshes(
        factory, limit=limit, kinds=kinds, entity_ids=entity_ids, snapshot_key=snapshot_key,
    )
    for claim in claims:
        try:
            with source_build():
                async with asyncio.timeout(claim_timeout_seconds):
                    await refresh_claim(factory, claim)
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            # refresh_claim has unwound and stopped its heartbeat. Record the
            # timeout outside that cancelled context so retry state can commit.
            await fail_claim(factory, claim, error)
            logger.warning("read_projection.timed_out kind=%s entity=%s", claim.kind, claim.entity_id)
        except Exception as error:
            # Retry state is durable; never log source payloads or DB URLs.
            logger.warning(
                "read_projection.failed kind=%s entity=%s error=%s",
                claim.kind,
                claim.entity_id,
                type(error).__name__,
            )
    return len(claims)


async def watch_refreshes(stop, *, factory=SessionFactory, interval=2, kinds=None, **claim_filters):
    last_storage_warning = 0.0
    while not stop.is_set():
        # Compose stores worker and PostgreSQL volumes on the same host disk.
        # Leave import/API headroom; pending work is retained in PostgreSQL.
        minimum = get_settings().read_projection_min_free_gib * 1024**3
        if shutil.disk_usage(".").free < minimum:
            if time.monotonic() - last_storage_warning > 60:
                logger.warning("read_projection.storage_headroom_low; refresh paused")
                last_storage_warning = time.monotonic()
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except TimeoutError:
                pass
            continue
        try:
            worked = await refresh_batch(factory, kinds=kinds, **claim_filters)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("read_projection.poll_failed error=%s", type(error).__name__)
            worked = 0
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass


async def main(args):
    try:
        selected = get_settings().read_projection_kinds if getattr(args, "watch", False) else ()
        kinds = args.kind or list(selected or READ_PROJECTION_KINDS)
        if args.snapshot != "current":
            if re.fullmatch(r"\d+\.\d+\.\d+", args.snapshot) is None:
                raise ValueError("A snapshot must be current or a full game version")
            if args.kind and not set(args.kind) <= HISTORICAL_KINDS:
                raise ValueError("The selected kind does not support historical snapshots")
            kinds = [kind for kind in kinds if kind in HISTORICAL_KINDS]
        if args.estimate:
            estimates = [
                asdict(await estimate_backfill(SessionFactory, kind, args.snapshot))
                for kind in kinds
            ]
            print(
                json.dumps(
                    {
                        "estimates": estimates,
                        "estimated_bytes": sum(row["estimated_bytes"] for row in estimates),
                    }
                ),
                flush=True,
            )
            return
        if args.entity_id and (not args.backfill or len(kinds) != 1):
            raise ValueError("Pilot entity IDs require --backfill and exactly one --kind")
        if any(identity < 1 or identity > 9223372036854775807 for identity in args.entity_id):
            raise ValueError("Entity IDs must be positive PostgreSQL bigint identities")
        if len(args.entity_id) > 100:
            raise ValueError("A pilot supports at most 100 entity IDs")
        if args.backfill:
            async with SessionFactory() as session, session.begin():
                if args.snapshot != "current":
                    imported = await session.scalar(
                        select(GameRelease.id)
                        .join(
                            ImportRun,
                            ImportRun.release_id == GameRelease.id,
                        )
                        .where(
                            GameRelease.game_version == args.snapshot,
                            ImportRun.status == "succeeded",
                        )
                        .limit(1)
                    )
                    if imported is None:
                        raise ValueError("The selected snapshot has not been imported")
                for kind in kinds:
                    if args.entity_id:
                        await mark_dirty(session, kind, args.entity_id, args.snapshot, debounce_seconds=0)
                    else:
                        await mark_dirty(session, kind, [0], args.snapshot)
            print(json.dumps({"queued_scopes": len(kinds), "snapshot": args.snapshot}), flush=True)
        if args.watch:
            filters = {"entity_ids": args.entity_id, "snapshot_key": args.snapshot} if args.entity_id else {}
            if args.snapshot != "current":
                filters["snapshot_key"] = args.snapshot
            await watch_refreshes(asyncio.Event(), kinds=kinds, **filters)
        else:
            print(json.dumps({"claimed": await refresh_batch(
                kinds=kinds, entity_ids=args.entity_id or None, snapshot_key=args.snapshot,
            )}), flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument(
        "--estimate", action="store_true", help="Print storage estimates without queueing work"
    )
    parser.add_argument(
        "--entity-id",
        type=int,
        action="append",
        default=[],
        help="Queue pilot entities for one kind",
    )
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--snapshot", default="current")
    parser.add_argument("--kind", action="append", choices=READ_PROJECTION_KINDS)
    asyncio.run(main(parser.parse_args()))
