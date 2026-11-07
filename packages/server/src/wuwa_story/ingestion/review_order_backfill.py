"""Audit/rebuild imported archive sequences; never enqueue video/audio renders.

Dry-run by default. Example: --all-releases --repair-links --apply. Run as a
detached server job and retain stdout: each release commits independently;
link repair stages may commit independently; repeating a release is safe because
graph evidence and dirty scopes coalesce.
"""

import argparse
import asyncio
import json
import traceback
from collections import Counter

from sqlalchemy import select

from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.ops import GameRelease, ImportRun
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.cutscene_order import rebuild
from wuwa_story.ingestion.dungeon_backfill import backfill
from wuwa_story.ingestion.show_talk_sequence_backfill import backfill as backfill_sequences
from wuwa_story.read_models.refresh import mark_dirty
from wuwa_story.story.review_order import SOURCE, review_positions
from wuwa_story.story.service import _release_id
from wuwa_story.db.models.raw import SourceFile, SourceRecord


async def refresh_release(session, release, *, apply=False, repair_links=False, current_release_id=None):
    rows = list(await session.scalars(select(SourceRecord.data).join(SourceFile).where(
        SourceRecord.release_id == release.id, SourceFile.release_id == release.id,
        SourceFile.logical_source_path == SOURCE)))
    counts = Counter(row.get('QuestId')
                     for row in rows if type(row.get('QuestId')) is int)
    candidates = {row['QuestId'] for row in rows if type(row.get('QuestId')) is int and row['QuestId'] > 0
                  and counts[row['QuestId']] == 1 and review_positions(row.get('Data'))}
    identities = sorted(set(await session.scalars(select(Quest.node_id).join(NodeRevision,
                                                                             NodeRevision.node_id == Quest.node_id).where(NodeRevision.release_id == release.id,
                                                                                                                          Quest.game_quest_id.in_(candidates))))) if candidates else []
    report = {'release_id': release.id, 'game_version': release.game_version, 'apply': apply,
              'review_records': len(rows), 'quest_scopes': len(identities),
              'duplicate_quest_records': sum(count > 1 for count in counts.values())}
    if repair_links:
        report['links'] = await backfill(session, release.id, dry_run=not apply)
        report['sequence_links'] = await backfill_sequences(session, release.id, apply=apply, max_links=10000)
    if apply:
        for start in range(0, len(identities), 100):
            batch = identities[start:start + 100]
            await mark_dirty(session, 'quest', batch, release.game_version, debounce_seconds=0)
            if release.id == current_release_id:
                await mark_dirty(session, 'quest', batch, 'current', debounce_seconds=0)
        total, linked = await rebuild(session, release.id)
        report['catalog'] = {'scenes': total, 'linked': linked}
        await session.commit()
    report['queued_quest_scopes'] = len(identities) if apply else 0
    return report


async def main(args):
    failed = []
    try:
        async with SessionFactory() as session:
            current = await _release_id(session, None)
            query = select(GameRelease).where(select(ImportRun.id).where(
                ImportRun.release_id == GameRelease.id, ImportRun.status == 'succeeded').exists())
            if args.release_id:
                query = query.where(GameRelease.id.in_(args.release_id))
            releases = list(await session.scalars(query.order_by(GameRelease.sequence.desc())))
        if not releases or (args.release_id and {row.id for row in releases} != set(args.release_id)):
            raise ValueError(
                'Select releases with a successful snapshot import')
        for release in releases:
            try:
                async with SessionFactory() as session:
                    report = await refresh_release(session, release, apply=args.apply,
                                                   repair_links=args.repair_links, current_release_id=current)
                print(json.dumps(report), flush=True)
            except Exception as error:
                failed.append(release.id)
                print(json.dumps({'release_id': release.id, 'status': 'failed',
                                  'error_type': type(error).__name__,
                                  'trace': traceback.format_tb(error.__traceback__)}), flush=True)
        if failed:
            raise RuntimeError(
                f'Review rebuild failed for releases {failed}; rerun those explicit IDs')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--release-id', type=int, action='append')
    scope.add_argument('--all-releases', action='store_true')
    parser.add_argument('--repair-links', action='store_true')
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args()))
