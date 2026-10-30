"""Chronological source selection on an isolated PostgreSQL database."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.api.routes import nodes
from wuwa_story.db.repositories.story_browsing import latest_source_row
from wuwa_story.story.service import _latest_source_record

URL = os.getenv("WUWA_LATEST_SOURCE_TEST_DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not URL, reason="Set WUWA_LATEST_SOURCE_TEST_DATABASE_URL to an isolated PostgreSQL database")
async def test_latest_source_uses_numeric_version_and_same_version_sequence(monkeypatch):
    base = create_async_engine(URL)
    assert base.url.database.endswith("_verification"), "Use a dedicated verification database"
    prefix = "latest_source_" + uuid4().hex
    schemas = {name: f"{prefix}_{name}" for name in ("ops", "graph", "raw")}
    ops, graph, raw = (schemas[name] for name in ("ops", "graph", "raw"))
    engine = base.execution_options(schema_translate_map=schemas)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with base.begin() as connection:
            for schema in schemas.values():
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
            for sql in (
                f"CREATE TABLE {ops}.game_release (id bigint PRIMARY KEY, sequence int, game_version text, resource_version text, changelist bigint, upstream_name text, upstream_commit text, released_at timestamptz, imported_at timestamptz DEFAULT now(), metadata jsonb DEFAULT '{{}}')",
                f"CREATE TABLE {graph}.node_type (id smallint PRIMARY KEY, key text, description text)",
                f"CREATE TABLE {graph}.node_revision (id bigint PRIMARY KEY, node_id bigint, release_id bigint, revision smallint, content_hash bytea, source_record_id bigint, created_at timestamptz DEFAULT now(), metadata jsonb DEFAULT '{{}}')",
                f"CREATE TABLE {raw}.source_file (id bigint PRIMARY KEY, release_id bigint, file_object_id bigint, logical_source_path text, table_name text, schema_hash bytea, row_count bigint, metadata jsonb DEFAULT '{{}}')",
                f"CREATE TABLE {raw}.source_record (id bigint PRIMARY KEY, release_id bigint, source_file_id bigint, row_index bigint, source_key text, content_hash bytea, data jsonb, created_at timestamptz DEFAULT now())",
                f"INSERT INTO {ops}.game_release(id,sequence,game_version,upstream_name) VALUES (1,30,'3.9.0','x'),(2,5,'3.10.0','x'),(3,40,'3.5.0','x'),(4,7,'3.10.0','x'),(5,50,'unknown','x'),(6,6,'3.11.0','x')",
                f"INSERT INTO {graph}.node_type VALUES (1,'item',NULL)",
                f"INSERT INTO {raw}.source_file(id,release_id,logical_source_path) VALUES (1,1,'source.json')",
                f"INSERT INTO {raw}.source_record(id,release_id,source_file_id,row_index,source_key,content_hash,data) SELECT i,1,1,i,'key-'||i,decode('aa','hex'),jsonb_build_object('record',i) FROM generate_series(1,8) AS i",
                f"INSERT INTO {graph}.node_revision(id,node_id,release_id,revision,source_record_id,metadata) VALUES (1,1,1,30,1,'{{}}'),(2,1,2,5,2,'{{}}'),(3,1,3,40,3,'{{}}'),(4,1,4,7,4,'{{\"source_raw_path\":\"$.newest\"}}'),(5,1,5,50,5,'{{}}'),(6,1,6,6,NULL,'{{}}'),(7,3,NULL,10,7,'{{}}'),(8,3,NULL,20,8,'{{}}')",
            ):
                await connection.execute(text(sql))
        async with factory() as session:
            row = await latest_source_row(session, 1)
            assert (row[0].id, row[1].id, row[3].game_version) == (4, 4, "3.10.0")
            source = await _latest_source_record(session, 1)
            assert (source["version"], source["raw_path"], source["raw_record"]) == (
                "3.10.0", "$.newest", {"record": 4}
            )
            monkeypatch.setattr(nodes, "get_node", lambda *_: _node())
            detail = await nodes.node_source_record("item:1", session=session)
            assert (detail["source_record"]["id"], detail["source_record"]["version"]) == (4, "3.10.0")
            assert await latest_source_row(session, 2) is None
            unversioned = await latest_source_row(session, 3)
            assert (unversioned[0].id, unversioned[3]) == (8, None)
    finally:
        await engine.dispose()
        async with base.begin() as connection:
            for schema in schemas.values():
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await base.dispose()


async def _node():
    return SimpleNamespace(id=1, type_id=1, canonical_key="item:1")
