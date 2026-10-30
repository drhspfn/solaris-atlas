"""Run durable public projection refreshes independently of HTTP requests."""

import argparse
import asyncio
import json
import logging
import re
import shutil
import time
from dataclasses import asdict

from sqlalchemy import select

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.models.read_refresh import READ_PROJECTION_KINDS
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.policy import HISTORICAL_KINDS
from wuwa_story.read_models.refresh import (
    claim_refreshes,
    estimate_backfill,
    mark_dirty,
    refresh_claim,
)

logger = logging.getLogger(__name__)


async def refresh_batch(factory=SessionFactory, *, limit=1, kinds=None):
    claims = await claim_refreshes(factory, limit=limit, kinds=kinds)
    for claim in claims:
        try:
            with source_build():
                await refresh_claim(factory, claim)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Retry state is durable; never log source payloads or DB URLs.
            logger.warning(
                "read_projection.failed kind=%s entity=%s error=%s",
                claim.kind,
                claim.entity_id,
                type(error).__name__,
            )
    return len(claims)


async def watch_refreshes(stop, *, factory=SessionFactory, interval=2, kinds=None):
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
            worked = await refresh_batch(factory, kinds=kinds)
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
        kinds = args.kind or list(READ_PROJECTION_KINDS)
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
                    await mark_dirty(session, kind, args.entity_id or [0], args.snapshot)
            print(json.dumps({"queued_scopes": len(kinds), "snapshot": args.snapshot}), flush=True)
        if args.watch:
            await watch_refreshes(asyncio.Event(), kinds=kinds)
        else:
            print(json.dumps({"claimed": await refresh_batch(kinds=kinds)}), flush=True)
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
