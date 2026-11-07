"""Execute snapshot participation/ownership SQL against a small authored graph."""

import inspect
import json
import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import sqlite

from wuwa_story.api.routes.story import profiles
from wuwa_story.db.models.core import DialogueLine, Quest, QuestState, Speaker
from wuwa_story.db.models.graph import Edge, Node
from wuwa_story.db.repositories.character_identity import dialogue_participation, speaker_crosswalk
from wuwa_story.ingestion.speaker_crosswalk import PERSONAL_PORTRAIT_BASIS, SPEAKER_PATH
from wuwa_story.read_models import source_relations
from wuwa_story.read_models.context import source_build


class Rows(list):
    def all(self):
        return list(self)


@pytest.fixture
def graph():
    db = sqlite3.connect(':memory:')
    for schema in ('core', 'graph', 'ontology', 'raw'):
        db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
    for ddl in (
        'CREATE TABLE core.dialogue_line (node_id int, speaker_node_id int, action_node_id int, source_record_id int)',
        'CREATE TABLE core.speaker (node_id int, game_speaker_id int)',
        'CREATE TABLE core.character (node_id int, metadata JSON)',
        'CREATE TABLE core.quest (node_id int, game_quest_id int)',
        'CREATE TABLE core.quest_state (node_id int, state_key text, source_record_id int)',
        'CREATE TABLE graph.node (id int, canonical_key text)',
        'CREATE TABLE graph.node_revision (id int, node_id int, release_id int)',
        'CREATE TABLE graph.edge (id INTEGER PRIMARY KEY, from_node_id int, to_node_id int, relation_type_id int, layer text, basis text)',
        'CREATE TABLE graph.edge_evidence (id INTEGER PRIMARY KEY, edge_id int, release_id int, source_record_id int, source_file_path text, explanation text)',
        'CREATE TABLE ontology.relation_type (id int, key text)',
        'CREATE TABLE raw.source_record (id int, release_id int, row_index int, source_file_id int)',
        'CREATE TABLE raw.source_file (id int, logical_source_path text, release_id int)',
    ):
        db.execute(ddl)
    db.executescript('''
        INSERT INTO raw.source_record VALUES (1,1,3,1),(2,2,4,3),(3,1,5797,2);
        INSERT INTO raw.source_file VALUES (1,'flow.json',1),(2,'BinData/speaker/speaker.json',1),(3,'flow.json',2);
        INSERT INTO core.dialogue_line VALUES (1000,999,999,2),(1001,5,999,2),(1002,6,999,2),(1003,4,999,2);
        INSERT INTO core.speaker VALUES (4,400049),(5,400050),(6,600001);
        INSERT INTO graph.node VALUES (4,'speaker:400049'),(5,'speaker:400050'),(6,'speaker:600001');
        INSERT INTO core.quest VALUES (100,1000),(200,2000);
        INSERT INTO core.quest_state VALUES (201,'caption-state',2),(202,'new-only',2);
        INSERT INTO graph.node_revision VALUES (1,10,1),(2,11,1);
    ''')
    family = json.dumps({'public_identity': {'family': 'rover'}})
    db.executemany('INSERT INTO core.character VALUES (?,?)',
                   [(10, family), (11, family), (12, '{}')])
    kinds = {key: index for index, key in enumerate((
        'references_character', 'spoken_by', 'presents_talk_item', 'contains_action',
        'has_quest_node', 'plays_cutscene', 'has_transcript_state',
        'references_dungeon_flow_state', 'has_plot_step', 'presents_scene',
    ), 1)}
    db.executemany('INSERT INTO ontology.relation_type VALUES (?,?)', [
                   (v, k) for k, v in kinds.items()])

    def edge(a, b, kind, release=1, personal=False):
        identity = db.execute('INSERT INTO graph.edge (from_node_id,to_node_id,relation_type_id,layer,basis) VALUES (?,?,?,\'source\',?)',
                              (a, b, kinds[kind], 'exact_join' if kind == 'references_character' else 'explicit_reference')).lastrowid
        db.execute('INSERT INTO graph.edge_evidence (edge_id,release_id,source_record_id,source_file_path,explanation) VALUES (?,?,?,?,?)',
                   (identity, release, 3 if personal else release, SPEAKER_PATH, PERSONAL_PORTRAIT_BASIS + 'fixture' if personal else 'legacy card-only'))
        return identity

    for speaker, character in ((4, 10), (5, 11), (6, 12)):
        edge(speaker, character, 'references_character', personal=True)
    # Legacy Anke card must NOT establish identity.
    edge(4, 13, 'references_character')
    for line, speaker, action in ((1000, 4, 500), (1001, 5, 501), (1002, 6, 502)):
        edge(line, speaker, 'spoken_by')
        edge(action, line, 'presents_talk_item')
        edge(201, action, 'contains_action')
    edge(1003, 4, 'spoken_by', 2)
    edge(503, 1003, 'presents_talk_item', 2)
    edge(202, 503, 'contains_action', 2)
    edge(100, 300, 'has_quest_node')
    caption_hop = edge(300, 800, 'plays_cutscene')
    edge(800, 201, 'has_transcript_state')
    edge(200, 201, 'references_dungeon_flow_state')

    def rows(statement):
        query = str(statement.compile(dialect=sqlite.dialect(),
                    compile_kwargs={'literal_binds': True}))
        return Rows(db.execute(query).fetchall())

    yield Row(db=db, rows=rows, caption_hop=caption_hop)
    db.close()


def test_personal_proof_and_dialogue_hops_override_stale_typed_source_not_snapshot_scope(graph):
    crosswalk = speaker_crosswalk(1)
    assert set(graph.rows(select(crosswalk.c.speaker_id, crosswalk.c.character_id))) == {
        (4, 10), (5, 11), (6, 12)}
    participation = dialogue_participation(1, speaker_ids=[4])
    assert graph.rows(select(participation.c.line_id, participation.c.state_id,
                             participation.c.source_record_id)) == [(1000, 201, 1)]
    newer = dialogue_participation(2, speaker_ids=[4])
    assert graph.rows(select(newer.c.line_id)) == [(1003,)]
    # Every hop needs proof, even when the line and speaker exist in both snapshots.
    graph.db.execute(
        'DELETE FROM graph.edge_evidence WHERE edge_id IN (SELECT id FROM graph.edge WHERE from_node_id=201 AND to_node_id=500)')
    assert graph.rows(select(participation.c.line_id)) == []


def test_npc_interaction_includes_unspoken_lines_only_with_snapshot_evidence(graph):
    from wuwa_story.api.routes.story.npc_profile import interaction_dialogue

    graph.db.execute("INSERT INTO core.dialogue_line VALUES (1100, NULL, NULL, 1)")
    graph.db.execute("INSERT INTO graph.edge (id,from_node_id,to_node_id,relation_type_id,layer,basis) VALUES (5001,201,510,4,'source','explicit')")
    graph.db.execute("INSERT INTO graph.edge (id,from_node_id,to_node_id,relation_type_id,layer,basis) VALUES (5002,510,1100,3,'source','explicit')")
    graph.db.execute("INSERT INTO graph.edge_evidence (edge_id,release_id,source_record_id) VALUES (5001,1,1),(5002,1,1)")
    rows = set(graph.rows(interaction_dialogue(1, [201])))
    assert (1100, 201, None) in rows
    assert (1000, 201, 4) in rows
    graph.db.execute("DELETE FROM graph.edge_evidence WHERE edge_id=5002")
    assert (1100, 201, None) not in set(graph.rows(interaction_dialogue(1, [201])))


def test_personal_crosswalk_proof_cannot_borrow_another_releases_raw_record(graph):
    crosswalk = speaker_crosswalk(1, [10])
    assert graph.rows(select(crosswalk.c.speaker_id)) == [(4,)]
    graph.db.execute('UPDATE graph.edge_evidence SET source_record_id=2 WHERE edge_id IN '
                     '(SELECT id FROM graph.edge WHERE from_node_id=4 AND to_node_id=10)')
    assert graph.rows(select(crosswalk.c.speaker_id)) == []


def test_family_card_proof_uses_the_same_shared_reader_contract(graph):
    from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH

    graph.db.execute('UPDATE graph.edge_evidence SET explanation=? WHERE edge_id IN '
                     '(SELECT id FROM graph.edge WHERE from_node_id=4 AND to_node_id=10)',
                     (PERSONAL_PORTRAIT_BASIS + MAIN_ROLE_PATH + ' explicit family membership; RolePileIconAsset',))
    crosswalk = speaker_crosswalk(1, [10])
    assert graph.rows(select(crosswalk.c.speaker_id)) == [(4,)]


def test_guest_proof_requires_matching_source_and_release(graph):
    from wuwa_story.ingestion.speaker_crosswalk import PLOT_GUEST_BASIS, PLOT_GUEST_PATH

    graph.db.execute('INSERT INTO raw.source_file VALUES (4,?,1),(5,?,2)', (PLOT_GUEST_PATH, PLOT_GUEST_PATH))
    graph.db.execute('INSERT INTO raw.source_record VALUES (4,1,0,4),(5,2,0,5)')
    condition = 'edge_id IN (SELECT id FROM graph.edge WHERE from_node_id=4 AND to_node_id=10)'
    graph.db.execute(f'UPDATE graph.edge_evidence SET explanation=?,source_file_path=?,source_record_id=4 WHERE {condition}',
                     (PLOT_GUEST_BASIS + 'fixture', PLOT_GUEST_PATH))
    crosswalk = speaker_crosswalk(1, [10])
    assert graph.rows(select(crosswalk.c.speaker_id)) == [(4,)]
    for record_id, path in ((3, PLOT_GUEST_PATH), (5, PLOT_GUEST_PATH), (4, SPEAKER_PATH)):
        graph.db.execute(f'UPDATE graph.edge_evidence SET source_record_id=?,source_file_path=? WHERE {condition}',
                         (record_id, path))
        assert graph.rows(select(crosswalk.c.speaker_id)) == []


def test_guest_role_context_is_not_speaker_identity_proof(graph):
    from wuwa_story.ingestion.speaker_crosswalk import PLOT_GUEST_PATH, PLOT_GUEST_ROLE_BASIS

    graph.db.execute('INSERT INTO raw.source_file VALUES (4,?,1)', (PLOT_GUEST_PATH,))
    graph.db.execute('INSERT INTO raw.source_record VALUES (4,1,0,4)')
    graph.db.execute('UPDATE graph.edge_evidence SET explanation=?,source_file_path=?,source_record_id=4 '
                     'WHERE edge_id IN (SELECT id FROM graph.edge WHERE from_node_id=4 AND to_node_id=10)',
                     (PLOT_GUEST_ROLE_BASIS + 'fixture', PLOT_GUEST_PATH))
    crosswalk = speaker_crosswalk(1, [10])
    assert graph.rows(select(crosswalk.c.speaker_id)) == []


def test_sequence_actor_proof_requires_the_same_flowstate_source_and_release(graph):
    from wuwa_story.ingestion.sequence_speaker_crosswalk import FLOW_PATH, SEQUENCE_ACTOR_BASIS

    graph.db.execute('INSERT INTO raw.source_file VALUES (4,?,1),(5,?,2)', (FLOW_PATH, FLOW_PATH))
    graph.db.execute('INSERT INTO raw.source_record VALUES (4,1,0,4),(5,2,0,5)')
    condition = 'edge_id IN (SELECT id FROM graph.edge WHERE from_node_id=4 AND to_node_id=10)'
    graph.db.execute(f'UPDATE graph.edge_evidence SET explanation=?,source_file_path=?,source_record_id=4 WHERE {condition}',
                     (SEQUENCE_ACTOR_BASIS + 'actor mesh and default TalkID', FLOW_PATH))
    crosswalk = speaker_crosswalk(1, [10])
    assert graph.rows(select(crosswalk.c.speaker_id)) == [(4,)]
    graph.db.execute(f'UPDATE graph.edge_evidence SET source_record_id=5 WHERE {condition}')
    assert graph.rows(select(crosswalk.c.speaker_id)) == []


@pytest.mark.asyncio
async def test_relation_index_has_caption_dungeon_refs_and_excludes_every_own_family_role(graph, monkeypatch):
    async def execute(statement):
        return graph.rows(statement)

    async def scalars(statement):
        return [row[0] for row in graph.rows(statement)]

    session = Row(execute=execute, scalars=scalars, get=AsyncMock(return_value=Row(
        metadata_json={'public_identity': {'family': 'rover'}})))
    monkeypatch.setattr(source_relations.shared,
                        '_character_progression_materials', AsyncMock(return_value=[]))
    result = []
    await source_relations._character_relations(session, Row(id=10, canonical_key='character:1'), 1,
                                                lambda *args: result.append(args))
    assert {identity for kind, identity, _,
            _ in result if kind == 'quest'} == {100, 200}
    peers = [r for r in result if r[0] == 'character']
    assert len(peers) == 1 and peers[0][1] == 12
    assert peers[0][3]['quest_refs'] == [{'id': 100}, {'id': 200}]
    assert peers[0][3]['other_source_record_ids'] == [1]
    assert set(peers[0][3]['owner_dialogue_node_ids']) == {1000, 1001}
    graph.db.execute(
        'DELETE FROM graph.edge_evidence WHERE edge_id=?', (graph.caption_hop,))
    result.clear()
    await source_relations._character_relations(session, Row(id=10, canonical_key='character:1'), 1,
                                                lambda *args: result.append(args))
    assert {identity for kind, identity, _,
            _ in result if kind == 'quest'} == {200}


@pytest.mark.asyncio
async def test_profile_participation_and_shared_scene_refs_follow_real_caption_and_dungeon_sql(graph, monkeypatch):
    character = Row(canonical_name='Name', name_key_id=None, description=None, playable=True,
                    metadata_json={'role_type': 1})

    async def execute(statement):
        entity = statement.column_descriptions[0].get('entity')
        last = list(statement.selected_columns)[-1]
        if entity is Speaker:
            rows = graph.rows(statement.with_only_columns(Speaker.node_id, Speaker.game_speaker_id,
                                                          Node.canonical_key, last, maintain_column_froms=True))
            return [(Row(node_id=i, game_speaker_id=g), Row(canonical_key=k), r) for i, g, k, r in rows]
        if entity is Quest:
            rows = graph.rows(statement.with_only_columns(Quest.node_id, Quest.game_quest_id,
                                                          last, maintain_column_froms=True))
            return [(Row(node_id=i, game_quest_id=g), state) for i, g, state in rows]
        if entity is Edge:
            return Rows()
        return graph.rows(statement)

    async def scalars(statement):
        if statement.column_descriptions[0].get('entity') is QuestState:
            return [Row(node_id=i, state_key=k, source_record_id=r) for i, k, r in graph.rows(
                statement.with_only_columns(QuestState.node_id, QuestState.state_key, QuestState.source_record_id))]
        if 'graph.node_type' in str(statement):
            return []
        return [r[0] for r in graph.rows(statement)]

    session = Row(get=AsyncMock(return_value=character), execute=execute, scalars=scalars,
                  scalar=AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, 'get_node', AsyncMock(
        return_value=Row(id=10, canonical_key='character:1', slug=None)))
    monkeypatch.setattr(profiles, '_release_id', AsyncMock(return_value=1))
    monkeypatch.setattr(profiles, 'character_family',
                        AsyncMock(return_value=([10, 11], None)))
    for name in ('prefetch_node_labels', 'prefetch_dialogue'):
        monkeypatch.setattr(profiles, name, AsyncMock())
    for name in ('_localized', '_latest_source_record'):
        monkeypatch.setattr(profiles, name, AsyncMock(return_value=None))
    monkeypatch.setattr(
        profiles, '_character_progression_materials', AsyncMock(return_value=[]))
    monkeypatch.setattr(profiles, '_node_label', AsyncMock(
        side_effect=lambda _s, i, _l: {'id': i}))
    monkeypatch.setattr(profiles, '_shared_scene_dialogue',
                        AsyncMock(return_value=([], [])))
    monkeypatch.setattr(profiles, '_quest_info', AsyncMock(
        side_effect=lambda _s, q, *_: {'id': q.node_id}))
    result = await profiles.character_profile.__wrapped__('character:1', session=session)
    assert result['quests_with_dialogue'] == [{'id': 100}, {'id': 200}]
    peer, = result['co_present_characters']
    assert peer['character'] == {'id': 12}
    assert peer['shared_scenes'][0]['quest_refs'] == [{'id': 100}, {'id': 200}]
    assert peer['shared_scenes'][0]['source']['record_id'] == 1


@pytest.mark.asyncio
async def test_compact_hook_is_explicit_and_defaults_to_plain_none(monkeypatch):
    from wuwa_story.read_models.readers import character_connections

    route = profiles.character_profile.__wrapped__
    assert inspect.signature(
        route).parameters['connections_limit'].default is None
    compact = AsyncMock(return_value={'compact': True})
    monkeypatch.setattr(character_connections,
                        'compact_character_profile', compact)
    session = object()
    assert await route('character:1', session=session, connections_limit=5) == {'compact': True}
    compact.assert_awaited_once_with(session, 'character:1', 'en', None, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize('background', [False, True])
async def test_shared_scene_payload_uses_pinned_speaker_without_mutating_typed_line(monkeypatch, background):
    line = Row(node_id=1, speaker_node_id=999, localization_key_id=2, inline_text=None,
               game_talk_item_id='3', game_text_id='4', source_type='Talk', source_index=0)
    row = (line, None, None, None, None, None, 42)
    rows = Rows([(*row, False) if background else row])
    session = Row(execute=AsyncMock(return_value=rows))
    monkeypatch.setattr(profiles, 'prefetch_dialogue', AsyncMock())
    monkeypatch.setattr(profiles, '_dialogue_payload', AsyncMock(
        side_effect=lambda _s, r, **_: {'speaker_id': r[0].speaker_node_id, 'action': None}))
    with source_build() if background else nullcontext():
        _, rendered = await profiles._shared_scene_dialogue(
            session, select(DialogueLine), 'en', 1, snapshot_speakers=True)
    assert rendered == [{'speaker_id': 42, 'action': None}]
    assert line.speaker_node_id == 999
