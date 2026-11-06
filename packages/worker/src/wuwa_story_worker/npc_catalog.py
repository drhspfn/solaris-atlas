"""Build and import a source-versioned NPC supplement without replaying story imports."""

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select
from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.ingestion.canonical import CanonicalRecordAdapter
from wuwa_story.ingestion.canonical_import import import_canonical_edges, import_canonical_entities
from wuwa_story.ingestion.npc_scenario import import_npc_scenario_edges
from wuwa_story.ingestion.raw import import_raw_snapshot
from wuwa_story.read_models.refresh import mark_dirty
from wuwa_story.search.indexer import build_lexical_index
from wuwa_story.story.domain import version_key

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.client_assets import wait_workspace_lock
from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.npc import compile_npcs
from wuwa_story_worker.npc_native_sources import TABLES, export_native_sources
from wuwa_story_worker.tooling import tool_path

SCHEMA = 1


def npc_compiler_fingerprint():
    from wuwa_story.ingestion import npc_sources

    from wuwa_story_worker import npc_native_sources
    from wuwa_story_worker.compiler import npc

    digest = hashlib.sha256()
    for module in (npc_sources, npc_native_sources, npc):
        digest.update(module.__name__.encode())
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()


def build_npc_dataset(config: Path, output: Path, *, asset_version: str, download_id: str,
                      game_version: str):
    if not re.fullmatch(r'\d+\.\d+\.\d+', asset_version) or not re.fullmatch(r'[0-9a-f]{64}', download_id):
        raise ValueError('NPC source requires a pinned asset version and download ID')
    if not re.fullmatch(r'\d+\.\d+\.\d+', game_version):
        raise ValueError('NPC snapshot version must be major.minor.patch')
    manifest_path = output / 'manifest.json'
    identity = {'npc_schema': SCHEMA, 'asset_version': asset_version,
                'download_id': download_id, 'game_version': game_version,
                'compiler_sha256': npc_compiler_fingerprint()}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if any(manifest.get(key) != value for key, value in identity.items()):
            raise ValueError('NPC dataset does not match pinned source identity')
        return manifest
    if output.exists():
        raise ValueError('Refusing to replace an incomplete NPC dataset; retain it for diagnosis')
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output.name + '.staging-', dir=output.parent))
    source = staging / 'input'
    inventory = export_native_sources(config, source, asset_version)
    writer = Writer(staging, game_version, 'installed-wuwa-client', None)
    counts = compile_npcs(source, writer, {})
    writer.finish()
    if not counts.get('character'):
        raise ValueError('Native NPC source produced no characters')
    (staging / 'graphs').mkdir(exist_ok=True)
    (staging / 'graphs/global.jsonl').touch(exist_ok=True)
    prefix = f'ClientData/{asset_version}/{download_id}/'
    evidence = []
    coverage = []
    for entry in inventory['tables']:
        logical = prefix + entry['file']
        target = staging / 'raw-evidence' / logical
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source / entry['file'], target)
        evidence.append({'file': logical, 'bytes': target.stat().st_size,
                         'sha256': entry['sha256'], 'status': 'native_decoded'})
        coverage.append({'file': logical, 'record_count': entry['rows'], 'shape': 'array'})
    for path in [*(staging / 'entities').glob('*.jsonl'), staging / 'graphs/global.jsonl']:
        if not path.is_file():
            continue
        renamed = path.with_suffix('.tmp')
        with path.open(encoding='utf-8') as stream, renamed.open('w', encoding='utf-8') as out:
            for line in stream:
                row = json.loads(line)
                if isinstance(row.get('source'), dict):
                    row['source']['file'] = prefix + row['source']['file']
                elif isinstance(row.get('source'), str):
                    row['source'] = prefix + row['source']
                row['asset_version'] = asset_version
                out.write(json.dumps(row, ensure_ascii=False) + '\n')
        renamed.replace(path)
    (staging / 'raw-evidence/index.json').write_text(json.dumps(evidence), encoding='utf-8')
    (staging / 'coverage.json').write_text(json.dumps({'tables': coverage}), encoding='utf-8')
    manifest = {**identity, 'source_repository': 'installed-wuwa-client', 'source_commit': None,
                'schema_inventory_hash': hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
                'counts': counts}
    shutil.rmtree(source)
    manifest_path = staging / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    staging.replace(output)
    return manifest


async def import_npc_dataset(session, output: Path, release_id: int):
    """Bounded existing upserts; replay retains stable nodes and immutable raw rows."""
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    release = await session.get(GameRelease, release_id)
    if release is None or release.game_version != manifest['game_version']:
        raise ValueError('NPC target snapshot differs from compiled supplement')
    releases = list(await session.scalars(select(GameRelease)))
    latest = max(releases, key=lambda value: (version_key(value.game_version), value.sequence))
    if latest.id != release_id:
        raise ValueError('Current-client NPC supplement may only update the latest story snapshot')
    raw = await import_raw_snapshot(output, session, release_id, batch_size=250)
    adapter = CanonicalRecordAdapter(output)
    entities = await import_canonical_entities(adapter, session, release_id, release.sequence, batch_size=100)
    edges = await import_canonical_edges(adapter, session, release_id, batch_size=250,
                                         skip_missing_endpoints=True)
    entity_source = (f"ClientData/{manifest['asset_version']}/{manifest['download_id']}/"
                     "BinData/level_entity/levelentityconfig.json")
    scenario_edges = await import_npc_scenario_edges(session, release_id, manifest['game_version'], entity_source)
    ids = list(await session.scalars(select(Character.node_id).join(Node, Node.id == Character.node_id)
                                    .where(Node.canonical_key.startswith('character:npc:'))))
    # asyncpg accepts at most 32767 bind parameters; native NPC catalogs are
    # larger than playable rosters and must not form one giant INSERT.
    for offset in range(0, len(ids), 1000):
        await mark_dirty(session, 'character', ids[offset:offset + 1000])
    await session.commit()
    indexed = await build_lexical_index(session, release.game_version,
                                      categories={'character'}, release_id=release_id)
    return {'characters': entities, 'edges': edges, 'scenario_edges': scenario_edges,
            'raw': raw, 'indexed': indexed, 'asset_version': manifest['asset_version']}


async def npc_catalog(payload, parent, session):
    from wuwa_story_worker.release_media import client_root

    releases = list(await session.scalars(select(GameRelease)))
    latest = max(releases, key=lambda value: (version_key(value.game_version), value.sequence))
    if latest.id != payload['release_id']:
        return {'skipped': True, 'reason': 'Current-client NPC catalog belongs to latest snapshot'}, 'completed'
    await session.commit()
    root = client_root(parent)
    # Avoid extracting every configuration database for this focused backfill.
    asset_filter = '|'.join(f'ConfigDB/{name}' for name in sorted({row[0] for row in TABLES}))
    receipt = await export_assets(root, tool_path('WUWA_FMODEL_PATH'), asset_filter)
    configs = list((receipt.parent / 'files').rglob('db_blueprint.db'))
    if len(configs) != 1:
        raise ValueError('Expected one pinned client NPC configuration directory')
    output = root / f'npc-catalog-v{SCHEMA}-{latest.id}-{npc_compiler_fingerprint()[:16]}'
    async with wait_workspace_lock(root / 'npc-catalog-lock'):
        await asyncio.to_thread(build_npc_dataset, configs[0].parent, output,
                                asset_version=parent['asset_version'], download_id=parent['download_id'],
                                game_version=latest.game_version)
        result = await import_npc_dataset(session, output, latest.id)
    return result, 'completed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--asset-version', required=True)
    parser.add_argument('--download-id', required=True)
    parser.add_argument('--game-version', required=True)
    parser.add_argument('--apply-release-id', type=int)
    args = parser.parse_args()

    async def run():
        async with wait_workspace_lock(args.output.parent / 'npc-catalog-lock'):
            result = await asyncio.to_thread(build_npc_dataset, args.config, args.output,
                asset_version=args.asset_version, download_id=args.download_id,
                game_version=args.game_version)
            if args.apply_release_id is not None:
                from wuwa_story.db.session import SessionFactory
                async with SessionFactory() as session:
                    result = await import_npc_dataset(session, args.output, args.apply_release_id)
            print(json.dumps(result))
    asyncio.run(run())


if __name__ == '__main__':
    main()
