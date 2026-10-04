import sqlite3

from sqlalchemy import select
from sqlalchemy.dialects import sqlite

from wuwa_story.api.routes.story.shared import _quest_state_links


def test_forward_and_reverse_ownership_use_only_complete_source_paths():
    with sqlite3.connect(":memory:") as db:
        for schema in ("core", "graph", "ontology"):
            db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        db.execute("CREATE TABLE core.quest (node_id INTEGER)")
        db.execute("CREATE TABLE ontology.relation_type (id INTEGER, key TEXT)")
        db.execute("CREATE TABLE graph.edge (from_node_id INTEGER, to_node_id INTEGER, relation_type_id INTEGER, layer TEXT)")
        db.executemany("INSERT INTO core.quest VALUES (?)", [(1,), (2,)])
        db.executemany("INSERT INTO ontology.relation_type VALUES (?, ?)", [
            (1, "references_flow_state"), (2, "has_quest_node"),
            (3, "has_plot_step"), (4, "presents_scene"),
            (5, "contains_action"), (6, "plays_cutscene"), (7, "has_transcript_state"),
        ])
        db.executemany("INSERT INTO graph.edge VALUES (?, ?, ?, ?)", [
            (1, 101, 1, "source"),  # Direct quest reference.
            (1, 10, 2, "source"), (10, 102, 1, "source"),
            (1, 20, 3, "source"), (20, 30, 4, "source"), (30, 103, 1, "source"),
            (30, 101, 1, "source"),  # Duplicate path is deduplicated.
            (2, 103, 1, "source"),  # Multiple owners remain selectable.
            (1, 104, 1, "semantic"),  # An agent inference is not ownership.
            (1, 40, 3, "source"), (40, 50, 4, "semantic"), (50, 105, 1, "source"),
            (60, 106, 1, "source"),  # Orphan scene / same-flow state is not guessed.
            (101, 70, 5, "source"), (70, 80, 6, "source"), (80, 107, 7, "source"),
            (80, 108, 7, "semantic"),
        ])
        links = _quest_state_links()

        def rows(query):
            sql = str(query.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
            return set(db.execute(sql).fetchall())

        assert rows(select(links.c.state_id).where(links.c.quest_id == 1)) == {(101,), (102,), (103,), (107,)}
        assert rows(select(links.c.quest_id).where(links.c.state_id == 107)) == {(1,)}
        assert rows(select(links.c.quest_id).where(links.c.state_id == 103)) == {(1,), (2,)}
        for orphan in (104, 105, 106, 108):
            assert rows(select(links.c.quest_id).where(links.c.state_id == orphan)) == set()
