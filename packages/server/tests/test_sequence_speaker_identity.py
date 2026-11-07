from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import sequence_speaker_identity as repair


@pytest.mark.asyncio
async def test_repair_rejects_conflict_skips_missing_default_and_is_idempotent(monkeypatch):
    flow = SimpleNamespace(one_or_none=lambda: (7, {'Actions': []}))
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        game_version='3.7', upstream_name='upstream', upstream_commit='abc')),
        execute=AsyncMock(), commit=AsyncMock())
    evidence = set()
    candidates = [
        {'from': 'speaker:1', 'to': 'character:10', 'source': repair.FLOW_PATH,
         'raw_path': '$[7].a', 'crosswalk': {'resolution': 'default_talker'}},
        {'from': 'speaker:2', 'to': 'character:10', 'source': repair.FLOW_PATH,
         'raw_path': '$[7].b', 'crosswalk': {'resolution': 'cast_and_corroborated_name'}},
        {'from': 'speaker:3', 'to': 'character:10', 'source': repair.FLOW_PATH,
         'raw_path': '$[7].c', 'crosswalk': {'resolution': 'default_talker'}},
    ]
    monkeypatch.setattr(repair, 'sequence_speaker_links', lambda *args: candidates)
    async def execute(statement, *args):
        sql = str(statement)
        if sql.startswith('SELECT pg_advisory_xact_lock'):
            return []
        if 'localization_key' in sql:
            return []
        if 'edge_evidence' in sql and 'anon_1' in sql:
            return [(1, 99)]
        if 'edge_evidence' in sql:
            return evidence
        if 'node_revision' in sql:
            return [('speaker:1', 1), ('speaker:2', 2), ('character:10', 10)]
        if 'source_record.id =' in sql:
            return flow
        if 'source_record' in sql:
            return []
        raise AssertionError(sql)
    session.execute.side_effect = execute
    async def import_edges(adapter, session, release_id, batch_size):
        rows = list(adapter.iter_edges())
        evidence.update((r['from'], r['to'], r['source'], r['raw_path']) for r in rows)
        return len(rows)
    importer = AsyncMock(side_effect=import_edges)
    dirty = AsyncMock()
    monkeypatch.setattr(repair, 'import_canonical_edges', importer)
    monkeypatch.setattr(repair, 'mark_dirty', dirty)
    report = await repair.repair_sequence_speakers(session, 21, 45, {}, apply=True)
    assert (report['candidate_links'], report['rejected_links'], report['unresolved_links'],
            report['written']) == (3, 1, 1, 1)
    assert report['affected_characters'] == [10]
    assert [call.args[3] for call in dirty.await_args_list] == ['current', '3.7']
    again = await repair.repair_sequence_speakers(session, 21, 45, {}, apply=True)
    assert again['written'] == 0
    importer.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_flow_record_fails_before_writes():
    session = SimpleNamespace(get=AsyncMock(return_value=object()),
                              execute=AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: None)),
                              commit=AsyncMock())
    with pytest.raises(ValueError, match='Unknown flow source'):
        await repair.repair_sequence_speakers(session, 21, 999, {}, apply=True)
    session.commit.assert_not_awaited()
