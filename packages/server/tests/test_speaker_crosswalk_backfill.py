from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.ingestion import speaker_crosswalk_backfill as repair


def fixture(monkeypatch):
    evidence, typed = set(), set()
    known = {'speaker:400049': 40, 'speaker:800545': 80, 'character:1212': 12}
    rows = [(repair.ROLE_PATH, 141, {'Id': 1212, 'RoleHeadIconCircle': '/jingran'}),
            (repair.SPEAKER_PATH, 5797, {'Id': 400049, 'HeadIconAsset': '/jingran'}),
            (repair.SPEAKER_PATH, 6707, {'Id': 800545, 'HeadIconAsset': '/jingran'})]
    session = Row(get=AsyncMock(return_value=Row(game_version='3.7.0', upstream_name='source',
                                                upstream_commit='pinned')),
                  execute=AsyncMock(), commit=AsyncMock())

    async def execute(statement, *_):
        query = str(statement.compile(dialect=postgresql.dialect(),
                                      compile_kwargs={'literal_binds': True}))
        if query.startswith('SET LOCAL'):
            return []
        if 'graph.edge_evidence' in query:
            assert 'edge_evidence.release_id = 21' in query
            return evidence
        if 'raw.source_record' in query:
            assert 'source_record.release_id = 21' in query
            assert 'source_file.release_id = 21' in query
            return rows
        if 'node_revision' in query:
            assert 'node_revision.release_id = 21' in query
            return list(known.items())
        if 'speaker_entity_link' in query:
            return typed
        raise AssertionError(query)

    async def importer(adapter, selected_session, release_id):
        assert selected_session is session and release_id == 21
        for row in adapter.iter_edges():
            evidence.add((row['from'], row['to'], row['raw_path']))
            typed.add((known[row['from']], known[row['to']]))

    session.execute.side_effect = execute
    imported = AsyncMock(side_effect=importer)
    monkeypatch.setattr(repair, 'import_canonical_edges', imported)
    return session, imported, known, evidence, typed


@pytest.mark.asyncio
async def test_dry_run_and_selective_repair_are_idempotent(monkeypatch):
    session, imported, _, _, _ = fixture(monkeypatch)
    preview = await repair.backfill(session, 21)
    assert preview['candidate_links'] == preview['missing_links'] == 2
    assert preview['affected_characters'] == [12] and preview['role_ids'] == [1212]
    assert preview['speaker_ids'] == [400049, 800545]
    assert preview['written'] == 0
    imported.assert_not_awaited()
    session.commit.assert_not_awaited()
    assert (await repair.backfill(session, 21, [1212], apply=True))['written'] == 2
    assert (await repair.backfill(session, 21, [1212], apply=True))['written'] == 0
    imported.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_convenience_link_is_repaired_even_when_graph_evidence_exists(monkeypatch):
    session, imported, _, evidence, _ = fixture(monkeypatch)
    evidence.update({('speaker:400049', 'character:1212', '$[5797].HeadIconAsset'),
                     ('speaker:800545', 'character:1212', '$[6707].HeadIconAsset')})
    assert (await repair.backfill(session, 21, [1212], apply=True))['written'] == 2
    imported.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_requires_explicit_scope_and_snapshot_endpoints(monkeypatch):
    session, imported, known, _, _ = fixture(monkeypatch)
    with pytest.raises(ValueError, match='explicit role'):
        await repair.backfill(session, 21, apply=True)
    known.pop('speaker:800545')
    assert (await repair.backfill(session, 21, [1212]))['unresolved_links'] == 1
    with pytest.raises(ValueError, match='absent from the selected snapshot'):
        await repair.backfill(session, 21, [1212], apply=True)
    imported.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_and_unknown_role_fail_before_writes(monkeypatch):
    session, imported, _, _, _ = fixture(monkeypatch)
    with pytest.raises(ValueError, match='exceed max_links'):
        await repair.backfill(session, 21, [1212], apply=True, max_links=1)
    with pytest.raises(ValueError, match='absent from this release'):
        await repair.backfill(session, 21, [9999], apply=True)
    imported.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_field_proof_preserves_legacy_edges_and_typed_associations(monkeypatch):
    session, _, _, evidence, typed = fixture(monkeypatch)
    legacy = {('speaker:400049', 'character:1203', '$[5797]'),
              ('speaker:800545', 'character:1212', '$[6707]')}
    evidence.update(legacy)
    typed.add((40, 1203))
    result = await repair.backfill(session, 21, [1212], apply=True)
    assert result['written'] == 2
    assert legacy <= evidence and (40, 1203) in typed
    assert ('speaker:400049', 'character:1212', '$[5797].HeadIconAsset') in evidence
    assert ('speaker:800545', 'character:1212', '$[6707].HeadIconAsset') in evidence
    assert (await repair.backfill(session, 21, [1212], apply=True))['written'] == 0
