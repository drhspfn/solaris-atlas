"""Bounded dependency bootstrap; coarse invalidation stays on until activation.

Run ``python -m wuwa_story.read_models.event_assets --backfill --max-events 25``
repeatedly, then ``--activate``. No force/coverage bypass is provided.
"""

import argparse
import asyncio
import json

from sqlalchemy import text

from wuwa_story.db.session import SessionFactory

MISSING = """SELECT e.id FROM core.game_event e
    WHERE NOT EXISTS (SELECT 1 FROM ops.event_asset_coverage c
                      WHERE c.event_id=e.id AND c.covered)"""


async def _timeouts(session, timeout_seconds):
    if not 1 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds must be 1..120")
    await session.execute(
        text("SELECT set_config('statement_timeout', :value, true)"),
        {"value": str(timeout_seconds * 1000)},
    )
    await session.execute(text("SET LOCAL lock_timeout='3s'"))


async def backfill(factory=SessionFactory, *, max_events=25, timeout_seconds=8):
    if not 1 <= max_events <= 1000:
        raise ValueError("max_events must be 1..1000")
    async with factory() as session, session.begin():
        await _timeouts(session, timeout_seconds)
        identities = list(
            await session.scalars(
                text(MISSING + " ORDER BY e.id LIMIT :limit"), {"limit": max_events}
            )
        )
    processed = 0
    for identity in identities:
        # Separate commits bound lock duration and preserve earlier progress if
        # a single source exceeds its statement deadline. Retry is idempotent.
        async with factory() as session, session.begin():
            await _timeouts(session, timeout_seconds)
            await session.execute(
                text("SELECT ops.refresh_event_asset_dependencies(:identity)"),
                {"identity": identity},
            )
        processed += 1
    async with factory() as session, session.begin():
        await _timeouts(session, timeout_seconds)
        remaining = await session.scalar(text("SELECT count(*) FROM (" + MISSING + ") missing"))
    return {"processed": processed, "remaining": remaining}


async def activate(factory=SessionFactory, *, timeout_seconds=8):
    async with factory() as session, session.begin():
        await _timeouts(session, timeout_seconds)
        # Acquire at transaction entry, before rows. The common write gate
        # protects coverage; table SHARE locks would invert BEFORE-DML locking.
        await session.execute(text("SELECT pg_advisory_xact_lock(705,25)"))
        await session.execute(text("SELECT id FROM ops.event_asset_state WHERE id=1 FOR UPDATE"))
        missing = await session.scalar(text(MISSING + " ORDER BY e.id LIMIT 1"))
        if missing is not None:
            raise ValueError(f"Event asset coverage incomplete; first missing event: {missing}")
        await session.execute(text("UPDATE ops.event_asset_state SET active=true WHERE id=1"))
    return {"active": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--backfill", action="store_true")
    action.add_argument("--activate", action="store_true")
    parser.add_argument("--max-events", type=int, default=25)
    parser.add_argument("--timeout-seconds", type=int, default=8)
    args = parser.parse_args()
    operation = (
        backfill(max_events=args.max_events, timeout_seconds=args.timeout_seconds)
        if args.backfill
        else activate(timeout_seconds=args.timeout_seconds)
    )
    print(json.dumps(asyncio.run(operation)), flush=True)


if __name__ == "__main__":
    main()
