"""Repair retained quest links, optionally queue client structure and missing pictures."""

import argparse
import asyncio
import json

from sqlalchemy import select

from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.dungeon_backfill import backfill
from wuwa_story.ingestion.release_media import enqueue_release_media


async def main(first, last, dry_run, client_index, illustrations, complete=False):
    def version(value):
        parts = tuple(map(int, value.split('.')))
        if len(parts) != 3:
            raise ValueError('Use a full patch version, for example 2.0.0')
        return parts

    lower, upper = version(first), version(last)
    if lower > upper:
        raise ValueError('First patch must not be later than last patch')
    async with SessionFactory() as session:
        releases = list(await session.scalars(select(GameRelease).order_by(GameRelease.sequence)))
    for release in releases:
        if not lower <= version(release.game_version) <= upper:
            continue
        async with SessionFactory() as session:
            report = await backfill(session, release.id, dry_run=dry_run)
            report['plan'] = {'client_index': client_index or complete,
                              'illustrations': illustrations or complete,
                              'cutscene_media': complete, 'standalone_voices': False}
            if not dry_run:
                if client_index or complete:
                    report['client_index'] = await enqueue_release_media(session, release.id, story_structure_only=True, repair_story=complete)
                if illustrations and not complete:
                    report['illustrations'] = await enqueue_release_media(session, release.id, illustrations_only=True)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first', default='1.0.0')
    parser.add_argument('--last', default='3.7.0')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--client-index', action='store_true')
    parser.add_argument('--illustrations', action='store_true')
    parser.add_argument('--complete', action='store_true', help='After client graph repair, queue story images, Spine backgrounds and cutscene media; reuse published targets')
    asyncio.run(main(**vars(parser.parse_args())))
