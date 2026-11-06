from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.ingestion import speaker_crosswalk_backfill as repair


def fixture(monkeypatch, *, family=False, guest=False):
    evidence, typed = set(), set()
    known = {'speaker:400049': 40, 'speaker:800545': 80, 'character:1212': 12}
    rows = [(repair.ROLE_PATH, 141, {'Id': 1212, 'RoleHeadIconCircle': '/jingran'}),
            (repair.SPEAKER_PATH, 5797, {'Id': 400049, 'HeadIconAsset': '/jingran'}),
            (repair.SPEAKER_PATH, 6707, {'Id': 800545, 'HeadIconAsset': '/jingran'})]
    if family:
        known = {'speaker:317': 317, 'speaker:1450': 1450, 'character:1501': 1501}
        rows = [(repair.MAIN_ROLE_PATH, 0, {'Id': 1501, 'Gender': 1, 'UnlockCondition': 0}),
                (repair.MAIN_ROLE_PATH, 1, {'Id': 1502, 'Gender': 0, 'UnlockCondition': 0}),
                (repair.ROLE_PATH, 3, {'Id': 1501, 'RoleType': 1, 'FormationRoleCard': '/male'}),
                (repair.ROLE_PATH, 6, {'Id': 1502, 'RoleType': 1, 'FormationRoleCard': '/female'}),
                (repair.SPEAKER_PATH, 410, {'Id': 317, 'RolePileIconAsset': '/female'}),
                (repair.SPEAKER_PATH, 1612, {'Id': 1450, 'RolePileIconAsset': '/male'})]
    if guest:
        known = {'speaker:108': 108, 'character:1402': 1402}
        rows = [(repair.ROLE_PATH, 1, {'Id': 1402, 'RoleType': 1, 'Name': 'role_name'}),
                (repair.SPEAKER_PATH, 0, {'Id': 108}),
                (repair.PLOT_GUEST_PATH, 0, {'GuestID': 1, 'Name': 'role_name', 'SpeakerID': [108]})]
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
            assert 'source_file.logical_source_path' in query
            assert repair.PLOT_GUEST_PATH in query and repair.PLOT_GUEST_BASIS in query
            return evidence
        if 'i18n.localization_key' in query:
            assert 'localization_value.release_id = 21' in query
            assert "locale.code = 'en'" in query and "localization_value.status = 'resolved_nonempty'" in query
            assert "'role_name'" in query and "'Speaker_108_Name'" in query
            return [('role_name', 'Yangyang'), ('Speaker_108_Name', 'Yangyang')]
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
            evidence.add((row['from'], row['to'], row['source'], row['raw_path']))
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
    evidence.update({('speaker:400049', 'character:1212', repair.SPEAKER_PATH, '$[5797].HeadIconAsset'),
                     ('speaker:800545', 'character:1212', repair.SPEAKER_PATH, '$[6707].HeadIconAsset')})
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
    legacy = {('speaker:400049', 'character:1203', repair.SPEAKER_PATH, '$[5797]'),
              ('speaker:800545', 'character:1212', repair.SPEAKER_PATH, '$[6707]')}
    evidence.update(legacy)
    typed.add((40, 1203))
    result = await repair.backfill(session, 21, [1212], apply=True)
    assert result['written'] == 2
    assert legacy <= evidence and (40, 1203) in typed
    assert ('speaker:400049', 'character:1212', repair.SPEAKER_PATH, '$[5797].HeadIconAsset') in evidence
    assert ('speaker:800545', 'character:1212', repair.SPEAKER_PATH, '$[6707].HeadIconAsset') in evidence
    assert (await repair.backfill(session, 21, [1212], apply=True))['written'] == 0


@pytest.mark.asyncio
async def test_mainrole_family_card_backfill_is_typed_evidenced_and_idempotent(monkeypatch):
    session, imported, _, evidence, typed = fixture(monkeypatch, family=True)
    preview = await repair.backfill(session, 21, [1501])
    assert preview['missing_links'] == 2 and preview['role_ids'] == [1501]
    assert preview['speaker_ids'] == [317, 1450]
    imported.assert_not_awaited()
    assert (await repair.backfill(session, 21, [1501], apply=True))['written'] == 2
    assert evidence == {('speaker:317', 'character:1501', repair.SPEAKER_PATH, '$[410].RolePileIconAsset'),
                        ('speaker:1450', 'character:1501', repair.SPEAKER_PATH, '$[1612].RolePileIconAsset')}
    assert typed == {(317, 1501), (1450, 1501)}
    assert (await repair.backfill(session, 21, [1501], apply=True))['written'] == 0


@pytest.mark.asyncio
async def test_guest_repair_does_not_confuse_source_files_and_is_idempotent(monkeypatch):
    session, imported, _, evidence, typed = fixture(monkeypatch, guest=True)
    evidence.add(('speaker:108', 'character:1402', repair.SPEAKER_PATH, '$[0].SpeakerID[0]'))
    typed.add((108, 1402))
    assert (await repair.backfill(session, 21, [1402], apply=True))['written'] == 1
    assert ('speaker:108', 'character:1402', repair.PLOT_GUEST_PATH, '$[0].SpeakerID[0]') in evidence
    assert (await repair.backfill(session, 21, [1402], apply=True))['written'] == 0
    imported.assert_awaited_once()
