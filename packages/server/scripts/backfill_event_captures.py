"""Review or backfill versioned event captures from consistent retained evidence."""

import argparse
import asyncio
import json

from sqlalchemy import text

from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.event_capture_backfill import backfill_event_captures


async def run(args):
    async with SessionFactory() as session:
        if not args.apply:
            await session.execute(text("SET TRANSACTION READ ONLY"))
        result = await backfill_event_captures(session, args.version, args.download_id,
            limit=args.limit, after_event_id=args.after_event_id, apply=args.apply)
        if args.apply:
            await session.commit()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--download-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-event-id", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(asyncio.run(run(parser.parse_args()))))


if __name__ == "__main__":
    main()
