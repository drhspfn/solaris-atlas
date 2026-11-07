"""Repair source-backed sequence speaker identities for one flow record."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select, text

from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.i18n import (
    Locale,
    LocalizationContent,
    LocalizationKey,
    LocalizationValue,
)
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories.character_identity import speaker_crosswalk
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_edges
from wuwa_story.ingestion.sequence_speaker_crosswalk import (
    FLOW_PATH,
    SEQUENCE_ACTOR_BASIS,
    sequence_speaker_links,
)
from wuwa_story.ingestion.speaker_crosswalk import ROLE_PATH
from wuwa_story.read_models.refresh import mark_dirty

PRELOAD_PATH = 'BinData/model_config_preload/modelconfigpreload.json'


def _speaker_keys(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return set()
    if isinstance(value, list):
        return set().union(*(_speaker_keys(item) for item in value))
    if not isinstance(value, dict):
        return set()
    result = {f'Speaker_{v}_Name' for k, v in value.items()
              if k in ('WhoId', 'defaultTalkID', 'TalkID') and type(v) is int and v > 0}
    for child in value.values():
        if isinstance(child, (dict, list)) or isinstance(child, str) and child[:1] in '[{':
            result.update(_speaker_keys(child))
    return result


async def repair_sequence_speakers(session, release_id, source_record_id, assets, *, apply=False):
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError('Unknown imported release')
    source = await session.execute(
        select(SourceRecord.row_index, SourceRecord.data).join(SourceFile).where(
            SourceRecord.id == source_record_id, SourceRecord.release_id == release_id,
            SourceFile.release_id == release_id, SourceFile.logical_source_path == FLOW_PATH))
    flow = source.one_or_none()
    if flow is None:
        raise ValueError('Unknown flow source record for selected release')

    async def rows(path, *filters):
        return list(await session.execute(
            select(SourceRecord.row_index, SourceRecord.data).join(SourceFile).where(
                SourceRecord.release_id == release_id, SourceFile.release_id == release_id,
                SourceFile.logical_source_path == path, *filters).order_by(SourceRecord.row_index)))

    roles = await rows(ROLE_PATH)
    mesh_ids = {raw.get('MeshId') for _, raw in roles if type(raw.get('MeshId')) is int}
    preloads = await rows(PRELOAD_PATH, SourceRecord.data['Id'].as_integer().in_(mesh_ids)) if mesh_ids else []
    keys = {raw['Name'] for _, raw in roles if isinstance(raw.get('Name'), str) and raw['Name']}
    keys.update(_speaker_keys(flow[1]))
    keys.update(_speaker_keys(list(assets.values())))
    english = {key: {'content': content} for key, content in await session.execute(
        select(LocalizationKey.key, LocalizationContent.content)
        .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
        .join(Locale, Locale.id == LocalizationValue.locale_id)
        .join(LocalizationContent, LocalizationContent.id == LocalizationValue.content_id)
        .where(LocalizationValue.release_id == release_id, Locale.code == 'en',
               LocalizationValue.status == 'resolved_nonempty', LocalizationKey.key.in_(keys))
    )} if keys else {}
    links = list(sequence_speaker_links(roles, preloads, [flow], assets, english))
    links = list({(r['from'], r['to'], r['source'], r['raw_path']): r for r in links}.values())
    node_keys = {r[side] for r in links for side in ('from', 'to')}
    known = {key: node_id for key, node_id in await session.execute(select(Node.canonical_key, Node.id).where(
        Node.canonical_key.in_(node_keys), select(NodeRevision.id).where(
            NodeRevision.node_id == Node.id, NodeRevision.release_id == release_id).exists()
    ))} if node_keys else {}
    unresolved = [r for r in links if r['from'] not in known or r['to'] not in known]
    required_unresolved = [r for r in unresolved
                           if r.get('crosswalk', {}).get('resolution') != 'default_talker']
    if apply and required_unresolved:
        raise ValueError('Crosswalk endpoints are absent from the selected snapshot')
    if apply:
        await session.execute(text('SELECT pg_advisory_xact_lock(718, :id)'), {'id': release_id})
    crosswalk = speaker_crosswalk(release_id)
    speakers = {known[r['from']] for r in links if r['from'] in known}
    proven = set(await session.execute(select(crosswalk.c.speaker_id, crosswalk.c.character_id)
                                       .where(crosswalk.c.speaker_id.in_(speakers)))) if speakers else set()
    owners = {}
    for speaker, character in proven:
        owners.setdefault(speaker, set()).add(character)
    candidate_owners = {}
    for r in links:
        if r not in unresolved:
            candidate_owners.setdefault(known[r['from']], set()).add(known[r['to']])
    rejected = [r for r in links if r not in unresolved and (
        owners.get(known[r['from']], set()) - {known[r['to']]}
        or len(candidate_owners[known[r['from']]]) > 1)]
    records = [{**r, 'version': release.game_version, 'source_repository': release.upstream_name,
                'source_commit': release.upstream_commit} for r in links if r not in unresolved and r not in rejected]
    source_node, target_node = Node.__table__.alias('source'), Node.__table__.alias('target')
    existing = set(await session.execute(
        select(source_node.c.canonical_key, target_node.c.canonical_key,
               EdgeEvidence.source_file_path, EdgeEvidence.source_raw_path)
        .select_from(Edge).join(RelationType, RelationType.id == Edge.relation_type_id)
        .join(source_node, source_node.c.id == Edge.from_node_id)
        .join(target_node, target_node.c.id == Edge.to_node_id)
        .join(EdgeEvidence, EdgeEvidence.edge_id == Edge.id)
        .where(Edge.layer == 'source', Edge.basis == 'exact_join',
               RelationType.key == 'references_character', EdgeEvidence.release_id == release_id,
               EdgeEvidence.source_record_id == source_record_id,
               EdgeEvidence.source_file_path == FLOW_PATH,
               EdgeEvidence.explanation.startswith(SEQUENCE_ACTOR_BASIS, autoescape=True),
               source_node.c.canonical_key.in_(node_keys), target_node.c.canonical_key.in_(node_keys))
    )) if records else set()
    missing = [r for r in records if (r['from'], r['to'], r['source'], r['raw_path']) not in existing]
    affected = sorted({known[r['to']] for r in missing})
    written = 0
    if apply and missing:
        # Queue refreshes in the same transaction the importer commits with the edges.
        for snapshot in ('current', release.game_version):
            for offset in range(0, len(affected), 1000):
                await mark_dirty(session, 'character', affected[offset:offset + 1000], snapshot)
        written = await import_canonical_edges(SimpleNamespace(iter_edges=lambda: iter(missing)),
                                               session, release_id, batch_size=len(missing))
        await session.commit()
    elif apply:
        await session.commit()  # Release the repair lock even when nothing is missing.
    return {'release_id': release_id, 'source_record_id': source_record_id,
            'candidate_links': len(links), 'unresolved_links': len(unresolved),
            'rejected_links': len(rejected), 'missing_links': len(missing), 'written': written, 'affected_characters': affected}


def load_assets(directory):
    assets = {}
    for path in sorted(Path(directory).rglob('*.json')):
        parts = path.relative_to(directory).parts
        matches = [i for i in range(len(parts) - 2) if parts[i:i + 2] == ('Client', 'Content')]
        if not matches:
            continue
        package = '/Game/' + '/'.join(parts[matches[0] + 2:]).removesuffix('.json')
        if package in assets:
            raise ValueError(f'Duplicate decoded asset: {package}')
        assets[package] = json.loads(path.read_text(encoding='utf-8-sig'))
    return assets


async def main(args):
    assets = load_assets(args.assets_dir)
    async with SessionFactory() as session:
        report = await repair_sequence_speakers(session, args.release_id, args.source_record_id,
                                                assets, apply=args.apply)
    # Keep CLI output bounded even for exceptionally large crosswalks.
    report['affected_characters_count'] = len(report['affected_characters'])
    report['affected_characters'] = report['affected_characters'][:1000]
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', type=int, required=True)
    parser.add_argument('--source-record-id', type=int, required=True)
    parser.add_argument('--assets-dir', required=True)
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args()))
