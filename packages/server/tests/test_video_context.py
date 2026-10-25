import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from wuwa_story.agents.video_context import (
    MAX_CONTEXT_CHARACTERS,
    MAX_CONTEXT_ROWS,
    _caption_query,
    video_context_snapshot,
)
from wuwa_story.db.models.storage import FileReference


def reference(**kwargs):
    return FileReference(
        owner_node_id=10,
        release_id=2,
        reference_type="cutscene_video",
        metadata_json={"asset_version": "3.7.0"},
        **kwargs,
    )


def session_with(rows=(), release=True):
    db = Mock()
    db.scalar = AsyncMock(
        side_effect=[
            10,
            SimpleNamespace(
                id=2,
                game_version="3.7.0",
                resource_version="resource-2",
                upstream_name="test",
                upstream_commit="abc",
            )
            if release
            else None,
        ]
    )
    result = Mock()
    result.mappings.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


async def test_snapshot_is_deterministic_json_with_source_provenance_and_no_inferred_timing():
    rows = [
        {
            "node_id": 50,
            "source_node": "caption:1:0",
            "revision_id": 7,
            "source_record_id": 8,
            "source_index": 0,
            "key": "Caption_A",
            "text": "Hello",
        }
    ]
    db = session_with(rows)
    snapshot = await video_context_snapshot(db, reference(), context="Ignore for cutscenes")
    assert snapshot["rows"] == rows
    assert snapshot["language"] == "en"
    assert snapshot["asset_version"] == snapshot["release"]["game_version"] == "3.7.0"
    assert snapshot["release"]["id"] == 2
    assert snapshot["release"]["upstream_commit"] == "abc"
    assert snapshot["truncated"] is False
    assert "context" not in snapshot
    assert json.loads(json.dumps(snapshot)) == snapshot
    assert snapshot == await video_context_snapshot(session_with(rows), reference())
    release_sql = str(
        db.scalar.call_args_list[1]
        .args[0]
        .compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "game_version = '3.7.0'" in release_sql
    assert "game_release.id = 2 DESC" in release_sql


@pytest.mark.parametrize("missing", ["version", "owner", "release", "asset_type"])
async def test_missing_exact_source_does_not_fall_back_to_caller_or_another_build(missing):
    ref = reference()
    db = session_with(release=missing != "release")
    if missing == "version":
        ref.metadata_json = {}
    elif missing == "owner":
        ref.owner_node_id = None
    elif missing == "asset_type":
        db.scalar.side_effect = [None]
    snapshot = await video_context_snapshot(db, ref, context="Unrelated")
    assert snapshot["rows"] == []
    assert snapshot["release"] is None
    assert "context" not in snapshot
    db.execute.assert_not_called()


async def test_explicit_truncation_bounds_rows_and_serialized_characters():
    rows = [{"node_id": i, "text": '"\\\n' * 100} for i in range(MAX_CONTEXT_ROWS + 1)]
    snapshot = await video_context_snapshot(session_with(rows), reference())
    assert snapshot["truncated"] is True
    assert 0 < len(snapshot["rows"]) <= MAX_CONTEXT_ROWS
    assert len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True)) <= MAX_CONTEXT_CHARACTERS
    assert snapshot["rows"] == rows[: len(snapshot["rows"])]


async def test_plain_text_is_only_allowed_for_generic_videos_and_is_bounded():
    ref = reference()
    ref.reference_type = "video"
    db = Mock()
    snapshot = await video_context_snapshot(db, ref, context="English context " * 2000)
    assert snapshot["basis"] == "caller"
    assert snapshot["context"].startswith("English context")
    assert snapshot["truncated"] is True
    assert snapshot["release"] is None
    assert len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True)) <= MAX_CONTEXT_CHARACTERS
    db.assert_not_called()
    assert (await video_context_snapshot(db, ref))["basis"] == "unavailable"


def test_compiled_query_executes_with_exact_asset_release_and_resolved_english_only():
    # Execute the actual query against small attached schemas: every graph hop,
    # node revision, source record and localization must belong to release 2.
    with sqlite3.connect(":memory:") as db:
        for schema in ("graph", "ontology", "raw", "i18n"):
            db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
        db.executescript("""
            CREATE TABLE graph.edge (id INTEGER, from_node_id INTEGER, to_node_id INTEGER,
                                     relation_type_id INTEGER, layer TEXT);
            CREATE TABLE graph.edge_evidence (id INTEGER, edge_id INTEGER, release_id INTEGER);
            CREATE TABLE graph.node (id INTEGER, canonical_key TEXT);
            CREATE TABLE graph.node_revision (id INTEGER, node_id INTEGER, release_id INTEGER,
                                              source_record_id INTEGER);
            CREATE TABLE ontology.relation_type (id INTEGER, key TEXT);
            CREATE TABLE raw.source_record (id INTEGER, release_id INTEGER, row_index INTEGER, data TEXT);
            CREATE TABLE i18n.localization_key (id INTEGER, key TEXT);
            CREATE TABLE i18n.localization_value (key_id INTEGER, locale_id INTEGER,
                                                 release_id INTEGER, content_id INTEGER, status TEXT);
            CREATE TABLE i18n.localization_content (id INTEGER, content TEXT);
            CREATE TABLE i18n.locale (id INTEGER, code TEXT);
            INSERT INTO ontology.relation_type VALUES (1,'has_variant'),(2,'references_asset'),(3,'uses_caption');
            INSERT INTO graph.edge VALUES (1,20,30,1,'source'),(2,30,10,2,'source'),
                (3,20,50,3,'source'),(4,21,31,1,'source'),(5,31,11,2,'source'),(6,21,51,3,'source');
            INSERT INTO graph.edge_evidence VALUES (1,1,2),(2,2,2),(3,3,2),(4,4,2),(5,5,2),(6,6,2);
            INSERT INTO graph.node VALUES (50,'caption:1:0'),(51,'caption:2:1');
            INSERT INTO graph.node_revision VALUES (1,10,2,NULL),(2,20,2,NULL),(3,30,2,NULL),
                (4,50,2,8),(5,51,2,9),(6,11,2,NULL),(7,21,2,NULL),(8,31,2,NULL);
            INSERT INTO raw.source_record VALUES (8,2,0,'{"CaptionText":"a"}'),(9,2,1,'{"CaptionText":"b"}');
            INSERT INTO i18n.localization_key VALUES (1,'a'),(2,'b');
            INSERT INTO i18n.locale VALUES (1,'en'),(2,'ja');
            INSERT INTO i18n.localization_content VALUES (1,'Hello'),(2,'Other variant'),(3,'Japanese'),(4,'Other build');
            INSERT INTO i18n.localization_value VALUES (1,1,2,1,'resolved_nonempty'),
                (2,1,2,2,'resolved_nonempty'),(1,2,2,3,'resolved_nonempty'),(1,1,3,4,'resolved_nonempty');
        """)
        sql = str(
            _caption_query(10, 2).compile(
                dialect=sqlite.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )

        def rows():
            return db.execute(sql).fetchall()

        assert [row[-1] for row in rows()] == ["Hello"]
        # Duplicate evidence and another variant of the same group cannot duplicate captions.
        db.executescript("""
            INSERT INTO graph.edge_evidence VALUES (7,3,2);
            INSERT INTO graph.edge VALUES (7,20,32,1,'source'),(8,32,10,2,'source');
            INSERT INTO graph.node_revision VALUES (9,32,2,NULL);
            INSERT INTO graph.edge_evidence VALUES (8,7,2),(9,8,2);
        """)
        assert len(rows()) == 1
        # Pin each hop independently; an edge observed only in another build is excluded.
        for edge_id in (1, 2, 3):
            db.execute("UPDATE graph.edge SET layer='semantic' WHERE id IN (7,8)")
            db.execute("UPDATE graph.edge_evidence SET release_id=3 WHERE edge_id=?", (edge_id,))
            assert rows() == []
            db.execute("UPDATE graph.edge_evidence SET release_id=2 WHERE edge_id=?", (edge_id,))
        for table, column, predicate in (
            ("graph.node_revision", "release_id", "node_id=50"),
            ("graph.node_revision", "release_id", "node_id=30"),
            ("graph.node_revision", "release_id", "node_id=20"),
            ("graph.node_revision", "release_id", "node_id=10"),
            ("raw.source_record", "release_id", "id=8"),
            ("i18n.localization_value", "release_id", "key_id=1 AND locale_id=1 AND release_id=2"),
        ):
            db.execute("SAVEPOINT excluded")
            db.execute(f"UPDATE {table} SET {column}=3 WHERE {predicate}")
            assert rows() == []
            db.execute("ROLLBACK TO excluded")
        db.execute("UPDATE i18n.localization_value SET status='resolved_empty' WHERE content_id=1")
        assert rows() == []
