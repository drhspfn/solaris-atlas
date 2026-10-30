"""Snapshot and graph equivalence against an isolated PostgreSQL database."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.db.repositories.quest_scope import quest_state_links, quest_states_query

URL = os.getenv("QUEST_SCOPE_TEST_DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not URL, reason="Set QUEST_SCOPE_TEST_DATABASE_URL to an isolated PostgreSQL database")
async def test_quest_cte_matches_graph_and_uses_one_snapshot_during_write():
    engine = create_async_engine(URL)
    assert engine.url.database.endswith("_verification"), "Never run this setup against production"
    prefix = "quest_test_" + uuid4().hex
    schemas = {name: f"{prefix}_{name}" for name in ("core", "graph", "ontology")}
    core, graph, ontology = (schemas[name] for name in ("core", "graph", "ontology"))
    try:
        async with engine.begin() as connection:
            for schema in schemas.values():
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
            for sql in (
                f"CREATE TABLE {core}.quest (node_id bigint PRIMARY KEY)",
                f"CREATE TABLE {ontology}.relation_type (id int PRIMARY KEY, key text)",
                f"CREATE TABLE {graph}.edge (id int PRIMARY KEY, from_node_id bigint, to_node_id bigint, relation_type_id int, layer text)",
                f"CREATE TABLE {graph}.edge_evidence (id int PRIMARY KEY, edge_id int, release_id int)",
                f"INSERT INTO {core}.quest VALUES (1),(2)",
                f"INSERT INTO {ontology}.relation_type VALUES (1,'has_quest_node'),(2,'references_flow_state'),(3,'contains_action'),(4,'plays_cutscene'),(5,'has_transcript_state'),(6,'has_plot_step'),(7,'presents_scene')",
                f"INSERT INTO {graph}.edge VALUES (1,1,10,1,'source'),(2,10,100,2,'source'),(3,100,70,3,'source'),(4,70,80,4,'source'),(5,80,200,5,'source'),(6,1,20,6,'source'),(7,20,30,7,'source'),(8,30,100,2,'source'),(9,1,300,2,'semantic')",
                f"INSERT INTO {graph}.edge VALUES (10,2,11,1,'source'),(11,11,81,4,'source'),(12,81,201,5,'source')",
                f"INSERT INTO {graph}.edge_evidence SELECT id,id,9 FROM {graph}.edge WHERE id <> 5",
            ):
                await connection.execute(text(sql))

        query = quest_states_query(1, 9)
        compiled = str(query.compile(
            dialect=engine.sync_engine.dialect, schema_translate_map=schemas,
            render_schema_translate=True, compile_kwargs={"literal_binds": True},
        ))
        # Correlate the anchor to the gate so the graph is traversed only after
        # the concurrent commit, while the statement keeps its original snapshot.
        anchor = f"WHERE {core}.quest.node_id = 1"
        assert anchor in compiled
        delayed_query = compiled.replace(anchor, f"WHERE {core}.quest.node_id = 1 + gate.flag", 1)
        async with engine.connect() as reader, engine.connect() as writer:
            reader = await reader.execution_options(schema_translate_map=schemas)
            links = quest_state_links(9, quest_node_id=1)
            old = select(links.c.state_id).where(links.c.quest_id == 1)
            assert set(await reader.scalars(query)) == set(await reader.scalars(old)) == {100}
            assert set(await reader.scalars(quest_states_query(1, None))) == {100, 200}
            assert set(await reader.scalars(quest_states_query(999, 9))) == set()
            direct = quest_state_links(9, quest_node_id=2)
            assert set(await reader.scalars(quest_states_query(2, 9))) == {201}
            assert set(await reader.scalars(select(direct.c.state_id))) == {201}

            pid = await reader.scalar(text("SELECT pg_backend_pid()"))
            delayed = text(
                "WITH gate AS MATERIALIZED (SELECT pg_sleep(2) AS sleeping, 0 AS flag) "
                f"SELECT result.node_id FROM gate CROSS JOIN LATERAL ({delayed_query}) result"
            )
            running = asyncio.create_task(reader.execute(delayed))
            try:
                for _ in range(100):
                    sleeping = await writer.scalar(text(
                        "SELECT wait_event = 'PgSleep' FROM pg_stat_activity WHERE pid = :pid"
                    ), {"pid": pid})
                    if sleeping:
                        break
                    await writer.rollback()
                    await asyncio.sleep(0.02)
                else:
                    pytest.fail("Reader did not enter the in-flight statement")
                await writer.execute(text(
                    f"INSERT INTO {graph}.edge_evidence VALUES (5,5,9)"
                ))
                await writer.commit()
                assert set((await running).scalars()) == {100}
                assert set(await reader.scalars(query)) == {100, 200}
                assert set(await reader.scalars(old)) == {100, 200}
            finally:
                if not running.done():
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
    finally:
        async with engine.begin() as connection:
            for schema in schemas.values():
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()
