import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from wuwa_story.db.models.graph import Edge, EdgeEvidence
from wuwa_story.ingestion.canonical_import import import_canonical_edges

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.flow import compile_flow

ASSET = '/Game/Aki/Sequence/LevelA_Seq/Main/03_1/M0334/DA_M0334.DA_M0334'
SOURCE = 'BinData/flowState/flowstate.json'


@pytest.fixture
def compiled_sequences(tmp_path):
    actions = [
        {'Name': 'ShowTalk', 'Params': {
            'SequenceDataAsset': ASSET, 'SequenceNames': ['M0334_1Z'],
            'TalkItems': [{'Id': 1, 'Type': 'Talk', 'Options': [{'Actions': [
                {'Name': 'ShowTalk', 'Params': {
                    'SequenceDataAsset': ASSET, 'SequenceNames': ['DA_M0334.M0334_2A'],
                }},
            ]}]}],
        }},
        {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': ASSET, 'SequenceNames': []}},
        {'Name': 'PlaySequenceData', 'Params': {'Path': ASSET}},
        {'Name': 'ShowTalk', 'Params': {'SequenceNames': ['M0334_1Z']}},
    ]
    raw = {'StateKey': 'Story_28_1', 'Id': 1, 'Actions': json.dumps(actions)}
    for relative, rows in {
        SOURCE: [raw], 'BinData/flow/flow.json': [{'Id': 'Story_28', 'States': [1]}],
        'BinData/speaker/speaker.json': [], 'BinData/cgVedio/videodata.json': [{'CgName': 'M0334'}],
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows), encoding='utf-8')
    outputs = []
    for attempt in range(2):
        writer = Writer(tmp_path / f'out-{attempt}', '3.1.0', 'fixture', 'commit')
        compile_flow(tmp_path, writer, {})
        writer.finish()
        edges = [json.loads(line) for line in (writer.out / 'graphs/global.jsonl').read_text().splitlines()]
        assets = [json.loads(line) for line in
                  (writer.out / 'entities/asset_reference.jsonl').read_text().splitlines()]
        outputs.append((edges, assets))
        assert not any(row['code'] == 'duplicate_id' for row in writer.diagnostics)
    assert outputs[0] == outputs[1]
    return *outputs[0], raw


def test_showtalk_uses_stable_sequence_identity_and_exact_occurrence_provenance(compiled_sequences):
    edges, assets, _ = compiled_sequences
    asset, = assets
    assert asset['id'] == 'asset:ue:' + ASSET
    assert asset['source'] == {'file': SOURCE, 'row': 0,
                              'raw_path': '$[0].Actions<json>[0].Params.SequenceDataAsset'}
    refs = [edge for edge in edges if edge['type'] == 'plays_sequence_asset']
    assert len(refs) == 4
    assert all(ref['to'] == asset['id'] and ref['source'] == SOURCE for ref in refs)
    first = next(ref for ref in refs if ref['from'] == 'action:Story_28_1:0')
    assert first['raw_path'] == '$[0].Actions<json>[0].Params.SequenceDataAsset'
    assert first['basis'] == 'ShowTalk.Params.SequenceDataAsset'
    assert first['sequence_names'] == ['M0334_1Z']
    assert first['sequence_names_raw_path'] == '$[0].Actions<json>[0].Params.SequenceNames'
    nested = next(ref for ref in refs if ref['from'].startswith('nested_action:'))
    assert nested['raw_path'].endswith('.TalkItems[0].Options[0].Actions[0].Params.SequenceDataAsset')
    assert nested['sequence_names'] == ['DA_M0334.M0334_2A']
    empty = next(ref for ref in refs if ref['from'] == 'action:Story_28_1:1')
    assert empty['sequence_names'] == []
    legacy = next(ref for ref in refs if ref['from'] == 'action:Story_28_1:2')
    assert legacy['basis'] == 'Params.Path' and legacy['raw_path'].endswith('.Params.Path')
    assert not any(edge['type'] in ('plays_cutscene', 'has_transcript_state') for edge in edges)


@pytest.mark.asyncio
async def test_sequence_edges_replay_without_duplicates_and_keep_historical_evidence(compiled_sequences):
    edges, _, raw = compiled_sequences
    refs = [edge for edge in edges if edge['type'] == 'plays_sequence_asset']
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            for schema in ('ontology', 'graph', 'raw'):
                await connection.execute(text(f"ATTACH DATABASE ':memory:' AS {schema}"))
            for ddl in (
                'CREATE TABLE ontology.relation_type (id INTEGER PRIMARY KEY, key TEXT, label TEXT, description TEXT, inverse_relation_id INTEGER, category TEXT, directional BOOLEAN, created_at DATETIME, metadata JSON)',
                'CREATE TABLE graph.node (id INTEGER PRIMARY KEY, canonical_key TEXT)',
                'CREATE TABLE graph.edge (id INTEGER PRIMARY KEY, from_node_id INTEGER, relation_type_id INTEGER, to_node_id INTEGER, layer TEXT, basis TEXT, confidence FLOAT, created_release_id INTEGER, valid_from_release_id INTEGER, valid_to_release_id INTEGER, processor_run_id INTEGER, metadata JSON, UNIQUE(from_node_id, relation_type_id, to_node_id, layer, basis))',
                'CREATE TABLE graph.edge_evidence (id INTEGER PRIMARY KEY, edge_id INTEGER, release_id INTEGER, evidence_node_id INTEGER, source_record_id INTEGER, source_file_path TEXT, source_raw_path TEXT, explanation TEXT, created_at DATETIME, UNIQUE(edge_id, release_id, source_file_path, source_raw_path))',
                'CREATE TABLE raw.source_file (id INTEGER PRIMARY KEY, release_id INTEGER, logical_source_path TEXT)',
                'CREATE TABLE raw.source_record (id INTEGER PRIMARY KEY, release_id INTEGER, source_file_id INTEGER, row_index INTEGER, data JSON)',
                "INSERT INTO ontology.relation_type (id,key,label,metadata) VALUES (3,'plays_sequence_asset','Sequence','{}')",
            ):
                await connection.execute(text(ddl))
            keys = sorted({ref[field] for ref in refs for field in ('from', 'to')})
            await connection.execute(text('INSERT INTO graph.node VALUES (:id,:key)'),
                                     [{'id': i, 'key': key} for i, key in enumerate(keys, 1)])
            for release in (15, 21):
                await connection.execute(text('INSERT INTO raw.source_file VALUES (:id,:id,:path)'),
                                         {'id': release, 'path': SOURCE})
                await connection.execute(text('INSERT INTO raw.source_record VALUES (:record,:release,:release,0,:data)'),
                                         {'record': 1000 + release, 'release': release, 'data': json.dumps(raw)})
        async with AsyncSession(engine) as session:
            # Replaying a historical release after the latest one must share edges,
            # but retain independent, release-scoped source evidence.
            for release in (15, 21, 15, 21):
                adapter = SimpleNamespace(iter_edges=lambda: iter(refs))
                await import_canonical_edges(adapter, session, release)
                assert await session.scalar(select(func.count()).select_from(Edge)) == 4
            proofs = list(await session.scalars(select(EdgeEvidence)))
            assert len(proofs) == 8
            assert {(proof.release_id, proof.source_record_id) for proof in proofs} == {
                (15, 1015), (21, 1021),
            }
            assert {proof.source_raw_path for proof in proofs} == {ref['raw_path'] for ref in refs}
    finally:
        await engine.dispose()
