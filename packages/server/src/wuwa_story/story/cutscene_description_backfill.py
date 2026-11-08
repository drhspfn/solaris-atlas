"""Copy validated legacy quest summaries to independent scene documents; no AI calls."""

import argparse
import asyncio
import json

from sqlalchemy import select

from wuwa_story.agents.contracts import CutsceneDescription
from wuwa_story.agents.evidence import quest_fingerprint
from wuwa_story.db.models.content import Document, DocumentHead
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.story.cutscene_descriptions import publish_descriptions


async def backfill(session, release_ids, *, apply=False, limit=1000):
    kinds = {f'story-explanation:{identity}': identity for identity in release_ids}
    documents = list(await session.scalars(select(Document).join(
        DocumentHead, DocumentHead.document_id == Document.id).where(
        DocumentHead.document_type.in_(kinds)).order_by(Document.id).limit(limit)))
    results = []
    for document in documents:
        entries = (document.metadata_json or {}).get(
            'cutscene_descriptions') or []
        if not entries:
            continue
        release_id = kinds[document.document_type]
        quest = await session.get(Quest, document.node_id)
        if quest is None or document.source_hash != await quest_fingerprint(
            session, quest, release_id, include_visual=True,
        ):
            results.append({'document_id': document.id,
                           'status': 'stale_quest_source'})
            continue
        try:
            values = [CutsceneDescription.model_validate(
                entry) for entry in entries]
            if apply:
                # A failed source/report check rolls back this document only.
                async with session.begin_nested():
                    await publish_descriptions(session, values, release_id=release_id,
                                               locale_id=document.locale_id, source_document_id=document.id,
                                               processor_run_id=document.processor_run_id)
            results.append({'document_id': document.id, 'summaries': len(values),
                            'status': 'copied' if apply else 'candidate'})
        except ValueError:
            results.append({'document_id': document.id,
                           'status': 'invalid_or_stale_visual_source'})
    return {'apply': apply, 'scanned': len(documents), 'limit_reached': len(documents) == limit, 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--release-id', type=int, action='append')
    scope.add_argument('--all-releases', action='store_true')
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--apply', action='store_true',
                        help='Default is read-only inspection')
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error('Limit must be between 1 and 10000')

    async def run():
        try:
            async with SessionFactory() as session:
                ids = args.release_id or list(await session.scalars(select(GameRelease.id)))
                result = await backfill(session, ids, apply=args.apply, limit=args.limit)
                if args.apply:
                    await session.commit()
                print(json.dumps(result))
        finally:
            await engine.dispose()
    asyncio.run(run())


if __name__ == '__main__':
    main()
