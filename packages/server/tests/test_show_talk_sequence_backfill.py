import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.db.models.graph import Edge, EdgeEvidence, Node, NodeRevision
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.ingestion import show_talk_sequence_backfill as repair

ASSET = '/Game/Aki/Sequence/M0334/DA_M0334.DA_M0334'


class RepairSession(AsyncSession):
    statements = None

    async def get(self, entity, identity, **kwargs):
        if entity is GameRelease:
            return SimpleNamespace(id=identity, game_version=f'3.{identity}.0', upstream_name='fixture',
                                   upstream_commit=f'commit-{identity}') if identity in (15, 21) else None
        return await super().get(entity, identity, **kwargs)

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        if str(statement).startswith(('SET LOCAL', 'SELECT pg_advisory_xact_lock')):
            return []
        return await super().execute(statement, *args, **kwargs)


@pytest.fixture
async def session(monkeypatch):
    invalidation = AsyncMock(return_value=[11])
    monkeypatch.setattr(repair, 'invalidate_repaired_quests', invalidation)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for schema in ('graph', 'ontology', 'raw'):
            await connection.execute(text(f"ATTACH DATABASE ':memory:' AS {schema}"))
        for ddl in (
            'CREATE TABLE graph.node_type (id INTEGER PRIMARY KEY, key TEXT, description TEXT)',
            "CREATE TABLE graph.node (id INTEGER PRIMARY KEY, type_id INTEGER, canonical_key TEXT UNIQUE, slug TEXT, created_release_id INTEGER, status TEXT DEFAULT 'active', created_at DATETIME, updated_at DATETIME, metadata JSON)",
            'CREATE TABLE graph.node_revision (id INTEGER PRIMARY KEY, node_id INTEGER, release_id INTEGER, revision INTEGER, content_hash BLOB, source_record_id INTEGER, created_at DATETIME, metadata JSON, UNIQUE(node_id,revision))',
            'CREATE TABLE ontology.relation_type (id INTEGER PRIMARY KEY, key TEXT, label TEXT, description TEXT, inverse_relation_id INTEGER, category TEXT, directional BOOLEAN, created_at DATETIME, metadata JSON)',
            'CREATE TABLE graph.edge (id INTEGER PRIMARY KEY, from_node_id INTEGER, relation_type_id INTEGER, to_node_id INTEGER, layer TEXT, basis TEXT, confidence FLOAT, created_release_id INTEGER, valid_from_release_id INTEGER, valid_to_release_id INTEGER, processor_run_id INTEGER, metadata JSON, UNIQUE(from_node_id, relation_type_id, to_node_id, layer, basis))',
            'CREATE TABLE graph.edge_evidence (id INTEGER PRIMARY KEY, edge_id INTEGER, release_id INTEGER, evidence_node_id INTEGER, source_record_id INTEGER, source_file_path TEXT, source_raw_path TEXT, explanation TEXT, created_at DATETIME, UNIQUE(edge_id, release_id, source_file_path, source_raw_path))',
            'CREATE TABLE raw.source_file (id INTEGER PRIMARY KEY, release_id INTEGER, logical_source_path TEXT)',
            'CREATE TABLE raw.source_record (id INTEGER PRIMARY KEY, release_id INTEGER, source_file_id INTEGER, row_index INTEGER, data JSON)',
            "INSERT INTO graph.node_type VALUES (2,'asset_reference',NULL)",
            "INSERT INTO graph.node (id,type_id,canonical_key,metadata) VALUES (1,1,'action:Story_28_1:0','{}')",
            "INSERT INTO ontology.relation_type (id,key,label,metadata) VALUES (3,'plays_sequence_asset','Sequence','{}')",
        ):
            await connection.execute(text(ddl))
        for release, row in ((15, 11812), (21, 11844)):
            await connection.execute(text('INSERT INTO raw.source_file VALUES (:id,:id,:path)'),
                                     {'id': release, 'path': repair.SOURCE})
            raw = {'StateKey': 'Story_28_1', 'Actions': json.dumps([
                {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': ASSET, 'SequenceNames': [str(release)]}},
            ])}
            await connection.execute(text('INSERT INTO raw.source_record VALUES (:id,:release,:release,:row,:data)'),
                                     {'id': 1000 + release, 'release': release, 'row': row, 'data': json.dumps(raw)})
            await connection.execute(text('INSERT INTO graph.node_revision (node_id,release_id,revision,source_record_id,metadata) VALUES (1,:release,:release,:id,\'{}\')'),
                                     {'release': release, 'id': 1000 + release})
        # Same record release but wrong source-file snapshot must be excluded.
        await connection.execute(text('INSERT INTO raw.source_record VALUES (9999,21,15,9999,:data)'),
                                 {'data': json.dumps(raw)})
    try:
        async with RepairSession(engine) as session:
            session.statements = []
            session.invalidation = invalidation
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dry_run_then_apply_and_replay_use_exact_snapshot_evidence(session):
    preview = await repair.backfill(session, 21, state_keys=['Story_28_1'])
    assert preview['source_rows'] == preview['missing_links'] == preview['missing_asset_nodes'] == 1
    assert preview['written'] == 0
    assert not any(getattr(statement, 'is_insert', False) for statement in session.statements)
    assert (await repair.backfill(session, 21, apply=True))['written'] == 1
    assert (await repair.backfill(session, 21, apply=True))['written'] == 0
    assert (await repair.backfill(session, 15, apply=True))['written'] == 1
    assert (await repair.backfill(session, 15, apply=True))['written'] == 0
    assert await session.scalar(select(func.count()).select_from(Node)) == 2
    assert await session.scalar(select(func.count()).select_from(Edge)) == 1
    proofs = list(await session.scalars(select(EdgeEvidence)))
    assert {(p.release_id, p.source_record_id, p.source_raw_path) for p in proofs} == {
        (21, 1021, '$[11844].Actions<json>[0].Params.SequenceDataAsset'),
        (15, 1015, '$[11812].Actions<json>[0].Params.SequenceDataAsset'),
    }
    asset = await session.scalar(select(Node).where(Node.canonical_key == 'asset:ue:' + ASSET))
    assert asset.metadata_json['source_row'] == 11844  # Historical replay must not overwrite latest metadata.
    revisions = list(await session.scalars(select(NodeRevision).where(NodeRevision.node_id == asset.id)))
    assert {(r.release_id, r.source_record_id) for r in revisions} == {(21, 1021), (15, 1015)}
    assert len({r.revision for r in revisions}) == 2
    assert [call.args[1].id for call in session.invalidation.await_args_list] == [21, 15]
    assert all(call.args[2] and {row['state_key'] for row in call.args[2]} == {'Story_28_1'}
               for call in session.invalidation.await_args_list)


@pytest.mark.asyncio
async def test_bound_is_checked_before_any_writes_and_unknown_owners_are_skipped(session):
    raw = {'StateKey': 'Uncompiled_1', 'Actions': json.dumps([
        {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': '/Game/Aki/Other.DA'}},
    ])}
    await session.execute(text('INSERT INTO raw.source_record VALUES (1022,21,21,11845,:data)'),
                          {'data': json.dumps(raw)})
    await session.commit()
    session.statements.clear()
    with pytest.raises(ValueError, match='exceed max_links'):
        await repair.backfill(session, 21, apply=True, max_links=1)
    assert not any(getattr(statement, 'is_insert', False) for statement in session.statements)
    preview = await repair.backfill(session, 21)
    assert preview['candidate_links'] == 2 and preview['uncompiled_owners'] == 1
    assert preview['missing_asset_nodes'] == 1


def test_source_parser_matches_compiler_nested_ownership_and_ignores_names_only():
    action = {'Name': 'ShowTalk', 'Params': {'TalkItems': [{'Options': [{'Actions': [
        {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': ASSET, 'SequenceNames': []}},
    ]}]}]}}
    rows = [(99, 7, {'StateKey': 'Story_28_1', 'Actions': json.dumps([
        action, {'Name': 'ShowTalk', 'Params': {'SequenceNames': ['M0334_1Z']}},
    ])})]
    link, = repair.sequence_links(rows)
    assert link['from'] == 'nested_action:Story_28_1:$[7].Actions<json>[0].Params.TalkItems[0].Options[0].Actions[0]'
    assert link['raw_path'] == '$[7].Actions<json>[0].Params.TalkItems[0].Options[0].Actions[0].Params.SequenceDataAsset'
    assert link['source_record_id'] == 99 and link['to'] == 'asset:ue:' + ASSET


@pytest.mark.asyncio
async def test_interrupted_batched_repair_redirties_and_resumes(session, monkeypatch):
    raw = {'StateKey': 'Story_28_1', 'Actions': json.dumps([
        {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': ASSET}},
        {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': '/Game/Aki/Other.DA'}},
    ])}
    await session.execute(text('UPDATE raw.source_record SET data=:data WHERE id=1021'), {'data': json.dumps(raw)})
    await session.execute(text("INSERT INTO graph.node (id,type_id,canonical_key,metadata) VALUES (3,1,'action:Story_28_1:1','{}')"))
    await session.execute(text("INSERT INTO graph.node_revision (node_id,release_id,revision,source_record_id,metadata) VALUES (3,21,1,1021,'{}')"))
    await session.commit()
    monkeypatch.setattr(repair, 'WRITE_BATCH_SIZE', 1)
    original = repair.import_canonical_edges
    calls = 0

    async def interrupt_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('interrupted')
        return await original(*args, **kwargs)

    monkeypatch.setattr(repair, 'import_canonical_edges', interrupt_second)
    with pytest.raises(RuntimeError, match='interrupted'):
        await repair.backfill(session, 21, apply=True, max_links=10000)
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(EdgeEvidence)) == 1
    monkeypatch.setattr(repair, 'import_canonical_edges', original)
    assert (await repair.backfill(session, 21, apply=True, max_links=10000))['written'] == 1
    assert (await repair.backfill(session, 21, apply=True, max_links=10000))['written'] == 0
    assert await session.scalar(select(func.count()).select_from(EdgeEvidence)) == 2
    assert session.invalidation.await_count == 3
