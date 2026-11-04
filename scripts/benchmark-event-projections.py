"""Read-only event projection parity and latency check; run with server dependencies."""

import argparse
import asyncio
import json
import statistics
import time

from fastapi.encoders import jsonable_encoder
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.events.composition import browse_events
from wuwa_story.read_models.readers.events import events_page


def identities(response):
    rows = jsonable_encoder(response)["events"]
    indexed = {(row["id"], row.get("occurrence_id")): row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate event occurrence identities")
    return indexed


async def benchmark(args):
    async def measure(projected):
        async with SessionFactory() as session:
            started = time.monotonic()
            if projected:
                response = await events_page(session, args.server, locale=args.locale, summary=True)
            else:
                response = await browse_events(args.server, None, None, args.locale, session, summary=True)
            return time.monotonic() - started, identities(response)

    try:
        samples, projected = [], {}
        for _ in range(args.samples):
            elapsed, projected = await measure(True)
            samples.append(round(elapsed, 3))
        report = {"server": args.server, "locale": args.locale, "events": len(projected),
                  "projection_seconds": samples, "median_seconds": statistics.median(samples)}
        matches = True
        if args.compare_source:
            elapsed, source = await measure(False)
            missing, extra = source.keys() - projected.keys(), projected.keys() - source.keys()
            changed = sum(source[key] != projected[key] for key in source.keys() & projected.keys())
            matches = not (missing or extra or changed)
            report.update(source_seconds=round(elapsed, 3), missing=len(missing), extra=len(extra),
                          changed=changed, equivalent=matches)
        print(json.dumps(report), flush=True)
        return 0 if matches else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", choices=("europe", "asia", "america"), default="europe")
    parser.add_argument("--locale", default="en")
    parser.add_argument("--samples", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--compare-source", action="store_true", help="Also run the expensive source renderer")
    raise SystemExit(asyncio.run(benchmark(parser.parse_args())))
