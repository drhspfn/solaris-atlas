import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import sqlite

from wuwa_story.db.repositories import cutscenes
from wuwa_story.story.cutscenes import descriptions


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
        [(30, 100)],  # Quest node owner.
        [(31, 40)],  # Plot scene -> plot step -> quest.
        [(40, 100)],
    ])
    monkeypatch.setattr(cutscenes, "source_parents", parent_calls)
    quest = SimpleNamespace(node_id=100)
    session = SimpleNamespace(execute=AsyncMock(
        side_effect=[[(10, 20), (11, 21)], [(quest, "quest-slug")]]))
    assert await cutscenes.owners(session, [9], 7) == [(9, quest, "quest-slug")]
    assert all(call.args[-1] == 7 for call in parent_calls.call_args_list)


@pytest.mark.asyncio
async def test_scene_title_does_not_consume_stale_analysis(monkeypatch):
    quest = SimpleNamespace(node_id=100)
    doc = SimpleNamespace(node_id=100, source_hash=b"old", metadata_json={
                          "cutscene_descriptions": [{"title": "Old scene"}]})
    session = SimpleNamespace(scalars=AsyncMock(
        return_value=[doc]), get=AsyncMock())
    monkeypatch.setattr(
        "wuwa_story.story.cutscenes.quest_fingerprint", AsyncMock(return_value=b"new"))
    assert await descriptions(session, [(9, quest, None)], 7, "en") == {}
    session.get.assert_not_awaited()
