import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.ingestion import dungeon_backfill as repair
from wuwa_story.ingestion.authored_flow_context import FLOWS, RECIPE, STATES, authored_flow_context


def fixture(monkeypatch, *, disappeared=False, stale=True, unchanged=False, ungrouped=False):
    tables = {
        repair.QUEST_NODES: [(7, {'Key': '42_3', 'Data': {'Condition': {
            'FlowListName': 'Story', 'FlowId': 6, 'StateId': 19}}})],
        FLOWS: [(11, {'Id': 'Story_6', 'States': [19, 20]})],
        STATES: [(12, {'Id': 19, 'StateKey': 'Story_6_19', 'Actions': '[]'}),
                 (13, {'Id': 20, 'StateKey': 'Story_6_20', 'Actions': json.dumps([
                     {'Name': 'PlayMovie', 'Params': {'VideoName': 'M0357'}}])})],
    }
    if disappeared:
        tables[FLOWS][0][1]['States'].remove(20)
    if ungrouped:
        tables[FLOWS] = []
        tables[STATES][0][1]['Actions'] = json.dumps([{'Name': 'PlayFlow', 'Params': {
            'FlowListName': 'Story', 'FlowId': 6, 'StateId': 20}}])
    old = json.dumps({'recipe': RECIPE, 'old_proof': True})
    if unchanged:
        link, = authored_flow_context(tables[repair.QUEST_NODES], tables[FLOWS], tables[STATES])
        old = link['basis']
    existing = [(80, 'references_scenario_flow_state', 'quest:42', 'flow_state:Story_6_20', STATES, '$[13].Actions')]
    # Only id80 belongs to this recipe/scope. Other recipe and quest evidence survive.
    stored = [(80, 'quest:42', 'flow_state:Story_6_20', STATES, '$[13].Actions', old),
              (81, 'quest:42', 'flow_state:Story_6_20', STATES, '$[99].Actions', '{"recipe":"other"}'),
              (82, 'quest:43', 'flow_state:Story_6_20', STATES, '$[13].Actions', old)] if stale else []
    queries = []

    async def execute(statement, *args):
        query = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        queries.append(query)
        if 'raw.source_record.row_index' in query:
            assert 'raw.source_record.release_id = 21' in query
            assert 'raw.source_file.release_id = 21' in query
            return next((rows for source, rows in tables.items() if repr(source) in query), [])
        if 'graph.edge_evidence.explanation' in query:
            assert "graph.edge.basis = 'inference'" in query and 'release_id = 21' in query
            assert 'graph.edge.from_node_id IN (SELECT graph.node.id' in query
            assert "'quest:42'" in query and 'quest/_node:42:' in query
            return stored
        if 'graph.edge_evidence.id, ontology.relation_type.key' in query:
            assert 'graph.edge.from_node_id IN (SELECT graph.node.id' in query
            return existing if stale else []
        return []

    async def scalars(statement):
        query = str(statement)
        if 'authored_tree_order' in query:
            return []
        if 'quest_state.state_key' in query:
            return ['Story_6_19', 'Story_6_20']
        return ['quest:42']

    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        id=21, game_version='3.7.0', upstream_name='fixture', upstream_commit='source')),
        execute=AsyncMock(side_effect=execute), scalars=AsyncMock(side_effect=scalars), commit=AsyncMock())
    imported = AsyncMock()
    invalidation = AsyncMock()
    monkeypatch.setattr(repair, 'import_canonical_edges', imported)
    monkeypatch.setattr(repair, 'rebuild_cutscene_order', AsyncMock())
    monkeypatch.setattr(repair, 'invalidate_repaired_quests', invalidation)
    return session, imported, invalidation, queries


@pytest.mark.asyncio
@pytest.mark.parametrize('disappeared', [False, True])
async def test_scoped_recipe_refresh_replaces_changed_proof_or_removes_disappeared_context(monkeypatch, disappeared):
    session, imported, invalidation, queries = fixture(monkeypatch, disappeared=disappeared)
    result = await repair.backfill(session, 21, quest_ids={42})
    deletions = [query for query in queries if query.startswith('DELETE')]
    assert len(deletions) == 1 and 'release_id = 21' in deletions[0] and ' IN (80)' in deletions[0]
    assert result['removed_context_evidence'] == 1
    assert result['written'] == (0 if disappeared else 1)
    if disappeared:
        imported.assert_not_awaited()
    else:
        adapter = imported.await_args.args[0]
        link, = adapter.iter_edges()
        assert json.loads(link['basis'])['recipe'] == RECIPE and link['confidence'] == 'possible'
    changed = invalidation.await_args.args[2]
    assert any(row.get('id') == 80 for row in changed)
    assert all(row['from'] == 'quest:42' for row in changed)


@pytest.mark.asyncio
async def test_dry_run_reports_obsolete_evidence_without_deletion_or_publication(monkeypatch):
    session, imported, invalidation, queries = fixture(monkeypatch)
    result = await repair.backfill(session, 21, dry_run=True, quest_ids={42})
    assert result['removed_context_evidence'] == result['written'] == 0
    assert len(result['stale_context_evidence']) == 1 and result['missing_evidence'] == 1
    assert not any(query.startswith('DELETE') for query in queries)
    imported.assert_not_awaited()
    invalidation.assert_not_awaited()


@pytest.mark.asyncio
async def test_equal_recipe_proof_is_idempotent_and_preserves_other_recipe_and_quest(monkeypatch):
    session, imported, invalidation, queries = fixture(monkeypatch, unchanged=True)
    result = await repair.backfill(session, 21, quest_ids={42})
    assert result['removed_context_evidence'] == result['written'] == 0
    assert result['stale_context_evidence'] == []
    assert not any(query.startswith('DELETE') for query in queries)
    imported.assert_not_awaited()
    invalidation.assert_not_awaited()


@pytest.mark.asyncio
async def test_equal_explicit_call_context_recipe_is_not_retracted_when_membership_is_missing(monkeypatch):
    session, imported, invalidation, queries = fixture(monkeypatch, ungrouped=True, unchanged=True)
    result = await repair.backfill(session, 21, quest_ids={42})
    assert result['authored_flow_context_links'] == 1
    assert result['removed_context_evidence'] == result['written'] == 0
    assert result['stale_context_evidence'] == []
    assert not any(query.startswith('DELETE') for query in queries)
    imported.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_change_invalidates_quest_cutscene_and_caption_dialogue_both_snapshots(monkeypatch):
    dirty = AsyncMock()
    monkeypatch.setattr('wuwa_story.read_models.refresh.mark_dirty', dirty)
    outputs = [[100], [200], [201], [300], [], [400], [401], [500, 501]]
    session = SimpleNamespace(scalars=AsyncMock(side_effect=outputs))
    quests = await repair.invalidate_repaired_quests(session, SimpleNamespace(id=21, game_version='3.7.0'),
        [{'from': 'quest:42', 'to': 'flow_state:Story_6_20'}])
    assert quests == [100]
    assert [call.args[1:] for call in dirty.await_args_list] == [
        ('quest', [100]), ('quest', [100], '3.7.0'),
        ('cutscene', [300]), ('cutscene', [300], '3.7.0'),
        ('dialogue', [500, 501]), ('dialogue', [500, 501], '3.7.0')]
    for call in session.scalars.await_args_list[2:]:
        assert 'release_id' in str(call.args[0])
