"""Resumable, bounded slug backfill: python -m wuwa_story.ingestion.slug_backfill."""

import argparse
import asyncio
import json

from sqlalchemy import text

from wuwa_story.db.session import SessionFactory

NODE_CANDIDATES = """
    SELECT n.id, d.title FROM graph.node n JOIN graph.node_type t ON t.id=n.type_id
    JOIN LATERAL (
      SELECT d.title FROM search.document d JOIN i18n.locale l ON l.id=d.locale_id
      WHERE d.target_node_id=n.id AND l.code='en'
        AND d.category IN ('character','item','area','quest','speaker')
        AND btrim(d.title) <> '' AND d.title <> n.canonical_key
        AND d.title NOT ILIKE '%please contact our customer service%'
      ORDER BY d.id LIMIT 1
    ) d ON true
    WHERE n.slug IS NULL AND t.key IN ('character','item','area','location','quest','speaker')
"""


def batch_sql(group: str) -> str:
    if group == "nodes":
        candidates = NODE_CANDIDATES + " ORDER BY n.id LIMIT :batch FOR UPDATE OF n SKIP LOCKED"
        table = "graph.node"
    elif group in {"events", "notices"}:
        table = "core.game_event" if group == "events" else "core.game_notice"
        candidates = f"SELECT id,title FROM {table} WHERE slug IS NULL ORDER BY id LIMIT :batch FOR UPDATE SKIP LOCKED"
    else:
        raise ValueError("Unknown slug group")
    return f"""WITH batch AS ({candidates})
        UPDATE {table} n SET slug=ops.public_slug(n.id,batch.title)
        FROM batch WHERE n.id=batch.id AND n.slug IS NULL RETURNING n.id"""


async def backfill(*, workers=2, batch_size=250, pause=0.1, dry_run=False):
    if not 1 <= workers <= 8 or not 1 <= batch_size <= 2000 or pause < 0:
        raise ValueError("Use 1..8 workers, a batch of 1..2000 and nonnegative pause")
    totals = {"nodes": 0, "events": 0, "notices": 0}
    if dry_run:
        async with SessionFactory() as session:
            for group in totals:
                query = NODE_CANDIDATES if group == "nodes" else (
                    f"SELECT id FROM core.{'game_event' if group == 'events' else 'game_notice'} WHERE slug IS NULL")
                totals[group] = await session.scalar(text(f"SELECT count(*) FROM ({query}) candidates"))
        return totals

    async def worker(number):
        for group in totals:
            while True:
                async with SessionFactory() as session:
                    await session.execute(text("SET LOCAL lock_timeout='500ms'"))
                    await session.execute(text("SET LOCAL statement_timeout='15s'"))
                    rows = list(await session.scalars(text(batch_sql(group)), {"batch": batch_size}))
                    await session.commit()
                if not rows:
                    break
                totals[group] += len(rows)
                print(json.dumps({"worker": number, "group": group, "updated": len(rows), "totals": totals}), flush=True)
                await asyncio.sleep(pause)
    await asyncio.gather(*(worker(i) for i in range(workers)))
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--pause", type=float, default=0.1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(backfill(workers=args.workers, batch_size=args.batch_size,
                                         pause=args.pause, dry_run=args.dry_run))))


if __name__ == "__main__":
    main()
