import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import sqlite

from wuwa_story.db.repositories import cutscenes
from wuwa_story.story.cutscenes import descriptions


@pytest.mark.asyncio
async def test_unowned_scene_returns_source_captions(monkeypatch):
    from wuwa_story.api.routes import cutscenes as route

    snapshot = SimpleNamespace(id=7, game_version="2.8.0")
    row = SimpleNamespace(node_id=9)
    monkeypatch.setattr(route, "release", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(route.repository, "scene", AsyncMock(return_value=row))
    monkeypatch.setattr(route, "summaries", AsyncMock(return_value=[{"quests": []}]))
    monkeypatch.setattr(route, "cutscene_flows", AsyncMock(return_value={9: {"asset_version": "3.7.0"}}))
    monkeypatch.setattr(route, "_links", AsyncMock(return_value={9: [{"node_id": 10}]}))
    monkeypatch.setattr(route, "caption_texts", AsyncMock(return_value={"line": "Hello"}))
    session = SimpleNamespace(scalars=AsyncMock(return_value=[
        {"CaptionText": "line", "ShowMoment": 30, "Duration": 60}
    ]))

    result = await route.detail("C0022", "2.8.0", "en", session)
    assert result["event"]["captions"]["en"] == [
        {"start": 1.0, "end": 3.0, "text": "Hello", "key": "line"}
    ]


def test_all_versions_sort_numerically_by_first_observation_without_duplicate_scenes():
    with sqlite3.connect(":memory:") as db:
        for schema in ("core", "graph", "ops"):
            db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        db.create_function("split_part", 3, lambda value, delimiter, index: value.split(delimiter)[index - 1])
        db.execute("CREATE TABLE core.cutscene (node_id INTEGER, cg_name TEXT)")
        db.execute("CREATE TABLE graph.node_revision (id INTEGER, node_id INTEGER, release_id INTEGER)")
        db.execute("CREATE TABLE ops.game_release (id INTEGER, game_version TEXT, sequence INTEGER)")
        db.execute("CREATE TABLE ops.import_run (release_id INTEGER, status TEXT)")
        db.executemany("INSERT INTO ops.game_release VALUES (?, ?, ?)",
                       [(1, "2.8.0", 10), (2, "2.9.0", 2), (3, "2.10.0", 3), (4, "3.0.0", 20)])
        db.executemany("INSERT INTO ops.import_run VALUES (?, ?)",
                       [(1, "succeeded"), (2, "succeeded"), (3, "succeeded"), (4, "running")])
        db.executemany("INSERT INTO core.cutscene VALUES (?, ?)",
                       [(10, "M010"), (20, "M020"), (30, "M030"), (40, "M040")])
        db.executemany("INSERT INTO graph.node_revision VALUES (?, ?, ?)",
                       [(1, 10, 1), (2, 10, 2), (3, 10, 3), (4, 20, 2),
                        (5, 20, 3), (6, 20, 3), (7, 30, 3), (8, 40, 4)])

        def rows(release_id=None, offset=0):
            query = cutscenes.archive_query(release_id, "", False).limit(2).offset(offset)
            sql = str(query.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
            return db.execute(sql).fetchall()

        assert rows() == [(30, "M030", "2.10.0", 3, "2.10.0"), (20, "M020", "2.9.0", 3, "2.10.0")]
        assert rows(offset=2) == [(10, "M010", "2.8.0", 3, "2.10.0")]
        assert rows(1) == [(10, "M010", "2.8.0", 3, "2.10.0")]


@pytest.mark.asyncio
async def test_source_parent_lookup_excludes_other_snapshots_and_inferred_links():
    with sqlite3.connect(":memory:") as db:
        for schema in ("graph", "ontology"):
            db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        db.execute("CREATE TABLE ontology.relation_type (id INTEGER, key TEXT)")
        db.execute(
            "CREATE TABLE graph.edge (id INTEGER, from_node_id INTEGER, to_node_id INTEGER, relation_type_id INTEGER, layer TEXT)")
        db.execute(
            "CREATE TABLE graph.edge_evidence (id INTEGER, edge_id INTEGER, release_id INTEGER)")
        db.execute(
            "INSERT INTO ontology.relation_type VALUES (1, 'plays_cutscene')")
        db.executemany("INSERT INTO graph.edge VALUES (?, ?, 9, 1, ?)",
                       [(1, 10, "source"), (2, 11, "source"), (3, 12, "semantic"), (4, 10, "source")])
        db.executemany("INSERT INTO graph.edge_evidence VALUES (?, ?, ?)",
                       [(1, 1, 7), (2, 2, 8), (3, 3, 7), (4, 4, 7)])

        class Session:
            async def execute(self, query):
                return db.execute(str(query.compile(dialect=sqlite.dialect(),
                                  compile_kwargs={"literal_binds": True}))).fetchall()

        assert await cutscenes.source_parents(Session(), [9], ("plays_cutscene",), 7) == [(9, 10)]
        assert await cutscenes.source_parents(Session(), [], ("plays_cutscene",), 7) == []


@pytest.mark.asyncio
async def test_catalog_resolves_only_complete_quest_owner_paths_and_deduplicates(monkeypatch):
    parent_calls = AsyncMock(side_effect=[
        [(9, 10), (9, 11)],  # Two actions play the same scene.
        [(20, 30), (21, 31), (20, 999)],
        [],  # No inferred scenario references.
        [(30, 100)],  # Quest node owner.
        [(31, 40)],  # Plot scene -> plot step -> quest.
        [(40, 100)],
    ])
    monkeypatch.setattr(cutscenes, "source_parents", parent_calls)
    quest = SimpleNamespace(node_id=100)
    session = SimpleNamespace(execute=AsyncMock(
        side_effect=[[(10, 20), (11, 21)], [(quest, "quest-slug")]]))
    assert await cutscenes.owners(session, [9], 7) == [(9, quest, "quest-slug", False)]
    assert all(call.args[-1] == 7 for call in parent_calls.call_args_list)


@pytest.mark.asyncio
async def test_catalog_marks_scenario_owner_as_inferred(monkeypatch):
    monkeypatch.setattr(cutscenes, "source_parents", AsyncMock(side_effect=[
        [(9, 10)], [], [(20, 100)], [], [], [],
    ]))
    quest = SimpleNamespace(node_id=100)
    session = SimpleNamespace(execute=AsyncMock(
        side_effect=[[(10, 20)], [(quest, "quest-slug")]]))
    assert await cutscenes.owners(session, [9], 7) == [(9, quest, "quest-slug", True)]


@pytest.mark.asyncio
async def test_scene_title_does_not_consume_stale_analysis(monkeypatch):
    quest = SimpleNamespace(node_id=100)
    doc = SimpleNamespace(node_id=100, source_hash=b"old", metadata_json={
                          "cutscene_descriptions": [{"title": "Old scene"}]})
    session = SimpleNamespace(scalars=AsyncMock(
        return_value=[doc]), get=AsyncMock())
    monkeypatch.setattr(
        "wuwa_story.story.cutscenes.quest_fingerprint", AsyncMock(return_value=b"new"))
    assert await descriptions(session, [(9, quest, None, False)], 7, "en") == {}
    session.get.assert_not_awaited()
