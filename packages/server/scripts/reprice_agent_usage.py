"""Dry-run by default; explicitly reprice inactive runs from recorded cache usage."""

import argparse
import asyncio
import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents.budget import rebuild_daily_usage, reprice_run
from wuwa_story.config.settings import get_settings


async def run(args: argparse.Namespace) -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            results = [
                await reprice_run(session, run_id, args.cached_rate, args.write_rate)
                for run_id in sorted(set(args.run_id))
            ]
            ledger = await rebuild_daily_usage(session) if args.repair_ledger else []
            if args.apply:
                await session.commit()
            else:
                await session.rollback()
            print(json.dumps({"applied": args.apply, "runs": results, "ledger": ledger}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, action="append", default=[])
    parser.add_argument("--cached-rate", type=Decimal, required=True)
    parser.add_argument("--write-rate", type=Decimal, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repair-ledger", action="store_true")
    asyncio.run(run(parser.parse_args()))
