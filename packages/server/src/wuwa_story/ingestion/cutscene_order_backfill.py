"""Rebuild cutscene ordering from existing completed snapshots without media exports."""
import argparse
import asyncio
import json

from sqlalchemy import select

from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.cutscene_order import rebuild
from wuwa_story.story.domain import version_key


async def main(first, last, dry_run):
    async with SessionFactory() as session:
        releases = list(await session.scalars(select(GameRelease).where(
            GameRelease.id.in_(select(ImportRun.release_id).where(
                ImportRun.status == "succeeded"))
        )))
    for release in sorted(releases, key=lambda item: version_key(item.game_version)):
        if not (version_key(first) <= version_key(release.game_version) <= version_key(last)):
            continue
        async with SessionFactory() as session:
            total, ranked = await rebuild(session, release.id)
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
            print(json.dumps({"version": release.game_version, "scenes": total,
                              "ranked": ranked, "dry_run": dry_run}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", default="1.0.0")
    parser.add_argument("--last", default="99.99.99")
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main(**vars(parser.parse_args())))
