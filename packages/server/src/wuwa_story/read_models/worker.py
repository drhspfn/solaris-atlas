"""Run durable public projection refreshes independently of HTTP requests."""

import argparse
import asyncio
import json
import logging
import re
import shutil
import signal
import time
from dataclasses import asdict

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.read_refresh import READ_PROJECTION_KINDS
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.policy import HISTORICAL_KINDS
from wuwa_story.read_models.refresh import (
    claim_refreshes,
    estimate_backfill,
    extract_sqlstate,
    fail_claim,
    mark_dirty,
    reconcile_ready_scopes,
    refresh_claim,
)

logger = logging.getLogger(__name__)
# Background-only DB budget; HTTP retains its separate statement limit.
# The overall refresh claim remains bounded at 600 seconds.
PROJECTION_STATEMENT_TIMEOUT_MS = 180_000
PROJECTION_LOCK_TIMEOUT_MS = 5_000
CLAIM_TIMEOUT_SECONDS = 10 * 60
DEFAULT_BATCH_LIMIT = 10
DEFAULT_BATCH_CONCURRENCY = 4


def create_worker_database(
    database_url=None, *, statement_timeout_ms=PROJECTION_STATEMENT_TIMEOUT_MS,
    lock_timeout_ms=PROJECTION_LOCK_TIMEOUT_MS,
):
    """Keep projection limits local to this engine, including pooled transactions."""
    if statement_timeout_ms < 1 or lock_timeout_ms < 1:
        raise ValueError("Worker database timeouts must be positive")
    worker_engine = create_async_engine(
        database_url or get_settings().database_url, pool_pre_ping=True,
        pool_size=10, max_overflow=5, pool_timeout=5,
    )

    @event.listens_for(worker_engine.sync_engine, "begin")
    def bounded_transaction(connection):
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{int(statement_timeout_ms)}ms'")
        connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")

    return worker_engine, async_sessionmaker(worker_engine, expire_on_commit=False, autoflush=False)


engine, SessionFactory = create_worker_database()


def _failure_codes(error):
    """Extract only bounded protocol codes, never exception messages or SQL."""
    status = getattr(error, "status_code", None) if isinstance(error, HTTPException) else None
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    return status, extract_sqlstate(error)


def _log_failure(claim, error, *, claim_timeout=False):
    status, sqlstate = _failure_codes(error)
    snapshot = claim.snapshot_key
    if not isinstance(snapshot, str) or not re.fullmatch(
        r"(?:current|[0-9]+(?:\.[0-9]+){1,3}|download:[0-9a-f]{1,64})", snapshot
    ):
        snapshot = "other"
    reason = "claim_deadline" if claim_timeout else "database_cancel_or_timeout" if sqlstate == "57014" else "none"
    logger.warning(
        "%s kind=%s entity=%s snapshot=%s error=%s http_status=%s sqlstate=%s timeout=%s",
        "read_projection.timed_out" if claim_timeout else "read_projection.failed",
        claim.kind, claim.entity_id, snapshot, type(error).__name__, status, sqlstate, reason,
        extra={"projection_kind": claim.kind, "projection_entity_id": claim.entity_id,
               "projection_snapshot": snapshot, "projection_error_type": type(error).__name__,
               "projection_http_status": status,
               "projection_sqlstate": sqlstate, "projection_timeout": reason},
    )


async def _run_claim(factory, claim, claim_timeout_seconds):
    deadline = asyncio.timeout(claim_timeout_seconds)
    try:
        with source_build():
            async with deadline:
                await refresh_claim(factory, claim)
    except asyncio.CancelledError:
        raise
    except TimeoutError as error:
        _log_failure(claim, error, claim_timeout=deadline.expired())
        if deadline.expired():
            # The build has unwound before the durable timeout is recorded.
            await fail_claim(factory, claim, error)
    except Exception as error:
        _log_failure(claim, error)


async def refresh_batch(
    factory=SessionFactory, *, limit=DEFAULT_BATCH_LIMIT, concurrency=DEFAULT_BATCH_CONCURRENCY,
    kinds=None, entity_ids=None, snapshot_key=None,
    claim_timeout_seconds=CLAIM_TIMEOUT_SECONDS,
):
    if claim_timeout_seconds <= 0:
        raise ValueError("Claim runtime timeout must be positive")
    if concurrency < 1:
        raise ValueError("Batch concurrency must be positive")
    claims = await claim_refreshes(
        factory, limit=limit, kinds=kinds, entity_ids=entity_ids, snapshot_key=snapshot_key,
    )
    if len(claims) <= 1 or concurrency == 1:
        for claim in claims:
            await _run_claim(factory, claim, claim_timeout_seconds)
        return len(claims)
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(claim):
        async with semaphore:
            await _run_claim(factory, claim, claim_timeout_seconds)

    await asyncio.gather(*(bounded(claim) for claim in claims))
    return len(claims)


async def watch_refreshes(stop, *, factory=SessionFactory, interval=2, kinds=None, **claim_filters):
    last_storage_warning = 0.0
    selected_kinds = list(dict.fromkeys(kinds if kinds is not None else READ_PROJECTION_KINDS))
    next_kind = 0
    busy_cycles = 0
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
        worked = 0
        for _ in range(len(selected_kinds)):
            if stop.is_set():
                break
            kind = selected_kinds[next_kind]
            next_kind = (next_kind + 1) % len(selected_kinds)
            try:
                worked = await refresh_batch(factory, kinds=[kind], **claim_filters)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("read_projection.poll_failed kind=%s error=%s", kind, type(error).__name__)
            if worked:
                break
        busy_cycles = busy_cycles + 1 if worked else 0
        if not stop.is_set() and (not worked or busy_cycles >= 20):
            busy_cycles = 0
            try:
                await reconcile_ready_scopes(factory, kinds=selected_kinds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("read_projection.reconcile_failed error=%s", type(error).__name__)
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass


async def main(args):
    loop = None
    installed_signals = []
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
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, stop.set)
                except NotImplementedError:  # Windows retains asyncio.run's SIGINT handling.
                    continue
                installed_signals.append(sig)
            filters = {"entity_ids": args.entity_id, "snapshot_key": args.snapshot} if args.entity_id else {}
            if args.snapshot != "current":
                filters["snapshot_key"] = args.snapshot
            await watch_refreshes(stop, kinds=kinds, **filters)
        else:
            print(json.dumps({"claimed": await refresh_batch(
                kinds=kinds, entity_ids=args.entity_id or None, snapshot_key=args.snapshot,
            )}), flush=True)
    finally:
        try:
            await engine.dispose()
        finally:
            for sig in installed_signals:
                loop.remove_signal_handler(sig)


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
