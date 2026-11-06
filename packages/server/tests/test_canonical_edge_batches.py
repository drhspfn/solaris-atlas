"""Execute the real metadata-bearing edge/link/evidence batches and replay them."""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.db.models.core import SpeakerEntityLink
from wuwa_story.db.models.graph import Edge, EdgeEvidence
from wuwa_story.ingestion.canonical_import import import_canonical_edges


@pytest.mark.asyncio
@pytest.mark.parametrize('batch_size', [1, 1000])
async def test_real_speaker_crosswalk_batches_preserve_metadata_and_are_idempotent(batch_size):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            for schema in ('ontology', 'graph', 'raw', 'core'):
                await connection.execute(text(f"ATTACH DATABASE ':memory:' AS {schema}"))
            for ddl in (
                "CREATE TABLE ontology.relation_type (id INTEGER PRIMARY KEY, key TEXT, label TEXT, description TEXT, inverse_relation_id INTEGER, category TEXT, directional BOOLEAN, created_at DATETIME, metadata JSON)",
                "CREATE TABLE graph.node (id INTEGER PRIMARY KEY, canonical_key TEXT)",
                "CREATE TABLE graph.edge (id INTEGER PRIMARY KEY, from_node_id INTEGER, relation_type_id INTEGER, to_node_id INTEGER, layer TEXT, basis TEXT, confidence FLOAT, created_release_id INTEGER, valid_from_release_id INTEGER, valid_to_release_id INTEGER, processor_run_id INTEGER, metadata JSON, UNIQUE(from_node_id, relation_type_id, to_node_id, layer, basis))",
                "CREATE TABLE core.speaker_entity_link (speaker_node_id INTEGER, entity_node_id INTEGER, resolution_type TEXT, confidence FLOAT, source_record_id INTEGER, metadata JSON, PRIMARY KEY(speaker_node_id, entity_node_id))",
                "CREATE TABLE graph.edge_evidence (id INTEGER PRIMARY KEY, edge_id INTEGER, release_id INTEGER, evidence_node_id INTEGER, source_record_id INTEGER, source_file_path TEXT, source_raw_path TEXT, explanation TEXT, created_at DATETIME, UNIQUE(edge_id, release_id, source_file_path, source_raw_path))",
                "CREATE TABLE raw.source_file (id INTEGER PRIMARY KEY, release_id INTEGER, logical_source_path TEXT)",
                "CREATE TABLE raw.source_record (id INTEGER PRIMARY KEY, release_id INTEGER, source_file_id INTEGER, row_index INTEGER)",
                "INSERT INTO ontology.relation_type (id,key,label,metadata) VALUES (3,'references_character','Character','{}')",
                "INSERT INTO graph.node VALUES (1,'speaker:400049'),(2,'speaker:800545'),(12,'character:1212')",
                "INSERT INTO raw.source_file VALUES (30,21,'BinData/speaker/speaker.json'),(31,20,'BinData/speaker/speaker.json')",
                "INSERT INTO raw.source_record VALUES (99,21,30,5797),(100,21,30,6707),(499,20,31,5797),(500,20,31,6707)",
            ):
                await connection.execute(text(ddl))
        records = [
            {'from': f'speaker:{speaker}', 'to': 'character:1212',
             'type': 'references_character', 'relation': 'exact_join',
             'basis': 'Exact typed personal portrait reference: fixture head asset',
             'crosswalk': {'method': 'exact_plot_guest_role', 'identity_status': 'suspect',
                           'warnings': ['conflicting_speaker_name']},
             'source': 'BinData/speaker/speaker.json',
             'raw_path': f'$[{row}].HeadIconAsset', 'version': '3.7.0'}
            for speaker, row in ((400049, 5797), (800545, 6707))
        ]
        adapter = SimpleNamespace(iter_edges=lambda: iter(records))
        async with AsyncSession(engine) as session:
            for _ in range(2):
                assert await import_canonical_edges(adapter, session, 21, batch_size=batch_size) == 2
                assert await session.scalar(select(func.count()).select_from(Edge)) == 2
                assert await session.scalar(select(func.count()).select_from(SpeakerEntityLink)) == 2
                assert await session.scalar(select(func.count()).select_from(EdgeEvidence)) == 2
            links = list(await session.scalars(select(SpeakerEntityLink).order_by(
                SpeakerEntityLink.speaker_node_id)))
            assert [link.source_record_id for link in links] == [99, 100]
            for link, record in zip(links, records, strict=True):
                assert link.entity_node_id == 12
                assert link.metadata_json == {
                    'basis': 'exact_join', 'evidence': record['basis'],
                    'source_file': record['source'], 'source_raw_path': record['raw_path'],
                    'version': '3.7.0',
                }
            proofs = list(await session.scalars(select(EdgeEvidence).order_by(EdgeEvidence.id)))
            assert [proof.source_record_id for proof in proofs] == [99, 100]
            assert all(proof.release_id == 21 for proof in proofs)
            edges = list(await session.scalars(select(Edge)))
            assert all(edge.metadata_json['crosswalk']['identity_status'] == 'suspect' for edge in edges)
            # Also compile the exact production PostgreSQL multi-row column keys.
            statement = postgresql.insert(SpeakerEntityLink.__table__).values([
                {'speaker_node_id': link.speaker_node_id, 'entity_node_id': link.entity_node_id,
                 'resolution_type': link.resolution_type, 'source_record_id': link.source_record_id,
                 'metadata': link.metadata_json} for link in links
            ]).on_conflict_do_nothing(index_elements=['speaker_node_id', 'entity_node_id'])
            compiled = statement.compile(dialect=postgresql.dialect())
            assert 'metadata' in str(compiled) and 'metadata_json' not in str(compiled)
            assert compiled.params['metadata_m0'] == links[0].metadata_json
    finally:
        await engine.dispose()
