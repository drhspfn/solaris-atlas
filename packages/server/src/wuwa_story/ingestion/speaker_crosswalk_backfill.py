"""Repair proven speaker identities from retained source rows, dry-run by default.

Select --release-id and optionally --role-id for a report. --apply requires an
explicit role selection and adds missing source evidence/convenience links only.
No sources/media are reimported and no projections are automatically refreshed.
"""

import argparse
import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select, text

from wuwa_story.db.models.core import SpeakerEntityLink
from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH
from wuwa_story.ingestion.speaker_crosswalk import (
    PERSONAL_PORTRAIT_BASIS,
    ROLE_PATH,
    SPEAKER_PATH,
    speaker_character_links,
)


async def backfill(session, release_id, role_ids=(), *, apply=False, max_links=500):
    if type(release_id) is not int or release_id <= 0:
        raise ValueError('A positive release ID is required')
    if any(type(identity) is not int or identity <= 0 for identity in role_ids):
        raise ValueError('Role IDs must be positive integers')
    if apply and not role_ids:
        raise ValueError('--apply requires explicit role IDs from a reviewed dry-run')
    if type(max_links) is not int or not 1 <= max_links <= 10000:
        raise ValueError('max_links must be between 1 and 10000')
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '30s'"))
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError('Unknown imported release')

    groups = {path: [] for path in (ROLE_PATH, SPEAKER_PATH, MAIN_ROLE_PATH)}
    for path, index, data in await session.execute(
        select(SourceFile.logical_source_path, SourceRecord.row_index, SourceRecord.data)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
               SourceFile.logical_source_path.in_(groups))
        .order_by(SourceFile.logical_source_path, SourceRecord.row_index)
    ):
        groups[path].append((index, data))
    if not groups[ROLE_PATH] or not groups[SPEAKER_PATH]:
        raise ValueError('This release has no complete RoleInfo/Speaker source tables')
    if not set(role_ids) <= {raw.get('Id') for _, raw in groups[ROLE_PATH]}:
        raise ValueError('Selected role IDs are absent from this release')
    candidates = [link for link in speaker_character_links(
        groups[ROLE_PATH], groups[SPEAKER_PATH], [raw for _, raw in groups[MAIN_ROLE_PATH]],
    ) if not role_ids or int(link['to'].split(':')[1]) in role_ids]
    if len(candidates) > max_links:
        raise ValueError('Candidate links exceed max_links; narrow the selected roles')
    keys = {link[side] for link in candidates for side in ('from', 'to')}
    known = {key: identity for key, identity in await session.execute(
        select(Node.canonical_key, Node.id).where(
            Node.canonical_key.in_(keys), select(NodeRevision.id).where(
                NodeRevision.node_id == Node.id, NodeRevision.release_id == release_id,
            ).exists(),
        )
    )} if keys else {}
    unresolved = [link for link in candidates if link['from'] not in known or link['to'] not in known]
    if apply and unresolved:
        raise ValueError('Crosswalk endpoints are absent from the selected snapshot')
    records = [{**link, 'version': release.game_version,
                'source_repository': release.upstream_name, 'source_commit': release.upstream_commit}
               for link in candidates if link not in unresolved]
    source, target = Node.__table__.alias('source'), Node.__table__.alias('target')
    existing = set(await session.execute(
        select(source.c.canonical_key, target.c.canonical_key, EdgeEvidence.source_raw_path)
        .select_from(Edge).join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(source, source.c.id == Edge.from_node_id).join(target, target.c.id == Edge.to_node_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .join(SourceRecord, SourceRecord.id == EdgeEvidence.source_record_id)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(RelationType.key == 'references_character', Edge.layer == 'source',
               Edge.basis == 'exact_join', EdgeEvidence.release_id == release_id,
               EdgeEvidence.source_file_path == SPEAKER_PATH,
               SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
               SourceFile.logical_source_path == SPEAKER_PATH,
               EdgeEvidence.explanation.startswith(PERSONAL_PORTRAIT_BASIS, autoescape=True),
               source.c.canonical_key.in_(keys), target.c.canonical_key.in_(keys))
    )) if records else set()
    typed = set(await session.execute(
        select(SpeakerEntityLink.speaker_node_id, SpeakerEntityLink.entity_node_id).where(
            SpeakerEntityLink.speaker_node_id.in_(known.values()),
            SpeakerEntityLink.entity_node_id.in_(known.values()),
        )
    )) if records else set()
    missing = [link for link in records if (link['from'], link['to'], link['raw_path']) not in existing
               or (known[link['from']], known[link['to']]) not in typed]
    if apply and missing:
        # Existing importer owns graph/evidence/link conflict handling. Replaying
        # an already evidenced edge also repairs its missing convenience link.
        await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(missing)), session, release_id)
        await session.commit()
    return {
        'release_id': release_id, 'game_version': release.game_version, 'applied': apply,
        'candidate_links': len(candidates), 'unresolved_links': len(unresolved),
        'missing_links': len(missing), 'written': len(missing) if apply else 0,
        'affected_characters': sorted({known[link['to']] for link in missing}),
        'role_ids': sorted({int(link['to'].split(':')[1]) for link in missing}),
        'speaker_ids': sorted({int(link['from'].split(':')[1]) for link in missing}),
    }


async def main(args):
    async with SessionFactory() as session:
        print(json.dumps(await backfill(session, args.release_id, args.role_id,
                                        apply=args.apply, max_links=args.max_links)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', type=int, required=True)
    parser.add_argument('--role-id', type=int, action='append', default=[])
    parser.add_argument('--max-links', type=int, default=500)
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args()))
