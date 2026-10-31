"""Incident-first neighbor queries preserve direction, evidence and pagination."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import Integer, LargeBinary, case, create_engine, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from wuwa_story.api.routes import nodes
from wuwa_story.db.models.graph import Edge, Node, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.source_sections import _request_caches
from wuwa_story.story import node_related as related_source


def legacy_incident(identity, direction):
    condition = (Edge.from_node_id == identity) | (Edge.to_node_id == identity)
    if direction == "in":
        condition = Edge.to_node_id == identity
    elif direction == "out":
        condition = Edge.from_node_id == identity
    return select(
        Edge.id.label("edge_id"), Edge.relation_type_id,
        case((Edge.from_node_id == identity, Edge.to_node_id), else_=Edge.from_node_id).label("neighbor_id"),
    ).where(condition, Edge.layer.in_(["source", "canonical"])).cte("legacy_incident")


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setattr(nodes.read_projections, "should_route_node_to_projection", lambda *a, **k: False)
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        for schema in ("graph", "ontology", "i18n", "search"):
            connection.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {schema}")
        for model in (Node, NodeType, Edge, RelationType, Locale, SearchDocument):
            columns = []
            for column in model.__table__.columns:
                datatype = (
                    "JSON" if isinstance(column.type, JSONB) else
                    "BLOB" if isinstance(column.type, LargeBinary) else
                    "TEXT" if isinstance(column.type, TSVECTOR) else
                    "INTEGER" if isinstance(column.type, Integer) else
                    column.type.compile(dialect=sqlite.dialect())
                )
                columns.append(f'"{column.name}" {datatype}' + (" PRIMARY KEY" if column.primary_key else ""))
            connection.exec_driver_sql(f"CREATE TABLE {model.__table__.fullname} ({','.join(columns)})")
        with Session(connection) as session:
            session.execute(NodeType.__table__.insert(), [
                {"id": 1, "key": "location"}, {"id": 2, "key": "character"},
                {"id": 3, "key": "source_reference"},
            ])
            session.execute(Node.__table__.insert(), [
                {"id": 1, "type_id": 1, "canonical_key": "location:1", "metadata": {}},
                {"id": 2, "type_id": 2, "canonical_key": "character:2", "metadata": {}},
                {"id": 3, "type_id": 3, "canonical_key": "source:3", "metadata": {"source_file": "A.json", "source_row": 4}},
                {"id": 4, "type_id": 3, "canonical_key": "source:4", "metadata": {"source_file": "B.json"}},
                {"id": 5, "type_id": 3, "canonical_key": "source:5", "metadata": {}},
            ])
            session.execute(RelationType.__table__.insert(), [
                {"id": 1, "key": "appears_in", "label": "Appears"},
                {"id": 2, "key": "references", "label": "References"},
            ])
            session.execute(Locale.__table__.insert(), [{"id": 1, "code": "en", "name": "English"}])
            session.execute(SearchDocument.__table__.insert(), [
                {"id": identity, "target_node_id": 2, "locale_id": locale,
                 "category": category, "title": title, "aliases": [], "body": "",
                 "content_hash": b"test"}
                for identity, locale, category, title in (
                    (1, 1, "character_nickname", "Nickname"),
                    (2, 1, "character", "Localized character"),
                    (3, 2, "character", "Other locale"),
                )
            ])
            # Bidirectional same-neighbor edges, self-loop, parallel provenance,
            # evidence in both directions, and excluded layers/unrelated edges.
            for identity, source, target, relation, layer in (
                (1, 1, 2, 1, "source"), (2, 2, 1, 1, "canonical"),
                (3, 1, 1, 1, "source"), (4, 1, 3, 2, "source"),
                (5, 3, 1, 2, "canonical"), (6, 1, 4, 2, "canonical"),
                (7, 5, 1, 1, "source"), (8, 1, 2, 1, "manual"),
                (9, 1, 3, 2, "semantic"), (10, 2, 3, 2, "source"),
                (11, 1, 2, 1, "canonical"),
            ):
                session.execute(Edge.__table__.insert().values(
                    id=identity, from_node_id=source, to_node_id=target,
                    relation_type_id=relation, layer=layer, basis="explicit_reference",
                    metadata={"edge": identity},
                ))
            session.flush()

            class Adapter:
                statements = []
                info = {}

                async def scalar(self, statement):
                    return session.scalar(statement)

                async def scalars(self, statement):
                    return session.scalars(statement)

                async def execute(self, statement):
                    self.statements.append(statement)
                    return session.execute(statement)

            yield Adapter()
    engine.dispose()


async def related(database, **kwargs):
    options = dict(direction="both", relation=None, category=None, source_file=None,
                   include_evidence=True, locale="en", limit=100, offset=0)
    return await nodes.node_related("location:1", session=database, **(options | kwargs))


@pytest.mark.parametrize("direction", ["both", "in", "out"])
@pytest.mark.parametrize("filters", [
    {}, {"include_evidence": False}, {"category": "source_reference"},
    {"category": "character"}, {"source_file": "A.json"},
    {"source_file": ""}, {"source_file": "absent"}, {"relation": "references"},
    {"limit": 2, "offset": 1}, {"limit": 2, "offset": 100},
])
async def test_sqlite_legacy_parity(database, monkeypatch, direction, filters):
    actual = await related(database, direction=direction, **filters)
    monkeypatch.setattr(related_source, "_incident_edges", legacy_incident)
    assert actual == await related(database, direction=direction, **filters)


async def test_self_loops_parallel_edges_and_evidence_scope(database):
    response = await related(database)
    results = response["results"]
    assert [row["provenance"]["edge"] for row in results] == [1, 2, 11, 3, 7, 4, 5, 6]
    assert {row["node"]["label"] for row in results if row["node"]["id"] == 2} == {"Localized character"}
    assert {row["node"]["label"] for row in results if row["node"]["id"] == 3} == {"A.json row 4"}
    loop = next(row for row in results if row["node"]["id"] == 1)
    assert loop["direction"] == "outgoing"
    incoming = await related(database, direction="in")
    assert [row["provenance"]["edge"] for row in incoming["results"]] == [2, 3, 7, 5]
    filtered = await related(database, category="character", source_file="absent", limit=1)
    assert filtered["results"] == []
    assert filtered["source_evidence"] == response["source_evidence"]
    assert {(row["source_file"], row["relation"], row["record_count"])
            for row in response["source_evidence"]} == {
        ("A.json", "references", 2), ("B.json", "references", 1), (None, "appears_in", 1),
    }


async def test_builder_two_full_pages_share_one_evidence_query_and_reset_cache(database):
    # A large incident set must remain complete; no arbitrary evidence/page cap.
    for identity in range(100, 1100):
        await database.execute(Node.__table__.insert().values(
            id=identity, type_id=3, canonical_key=f"source:{identity}",
            metadata={"source_file": "A.json"},
        ))
        await database.execute(Edge.__table__.insert().values(
            id=identity, from_node_id=1, to_node_id=identity, relation_type_id=2,
            layer="source", basis="explicit_reference", metadata={},
        ))
    database.statements.clear()
    original_cache = {(999, "both", None): []}
    database.info["node_related_evidence"] = original_cache
    with source_build(), _request_caches(database):
        first = await related(database, limit=500)
        second = await related(database, limit=500, offset=500)
        assert len(first["results"]) == len(second["results"]) == 500
        assert first["source_evidence"] == second["source_evidence"]
        assert sum(row["record_count"] for row in first["source_evidence"]) == 1004
        assert len(database.statements) == 3  # Two pages, one full summary.
        assert database.info["node_related_evidence"] is not original_cache
    assert database.info["node_related_evidence"] is original_cache
    with source_build(), _request_caches(database):
        await related(database, limit=500)
        assert len(database.statements) == 5  # New build recomputes summary.


async def test_public_requests_never_cache_evidence_and_builder_keys_filters(database):
    with source_build(), _request_caches(database):
        await related(database)
        await related(database, category="character", source_file="absent", locale="ja")
        assert len(database.statements) == 3  # Unrelated filters reuse summary.
        await related(database, direction="in")
        await related(database, relation="references")
        await nodes.node_related(
            "character:2", direction="both", relation=None, category=None,
            source_file=None, include_evidence=True, locale="en", limit=100,
            offset=0, session=database,
        )
        assert len(database.statements) == 9  # Direction, relation and owner differ.
    assert "node_related_evidence" not in database.info
    await related(database)
    await related(database)
    assert len(database.statements) == 13
    assert "node_related_evidence" not in database.info


@pytest.mark.parametrize("direction", ["both", "in", "out"])
async def test_postgres_queries_materialize_compact_incident_set(database, direction):
    await related(database, direction=direction)
    assert len(database.statements) == 2
    for statement in database.statements:
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "incident_edges AS MATERIALIZED" in sql
        assert "graph.node.id = incident_edges.neighbor_id" in sql
        cte = str(nodes._incident_edges(1, direction).element.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True},
        ))
        assert "metadata" not in cte
        assert ("UNION ALL" in cte) == (direction == "both")
        assert "graph.edge.layer IN ('source', 'canonical')" in cte


URL = os.getenv("WUWA_READ_REFRESH_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="Disposable local PostgreSQL URL required")
async def test_real_postgres_incident_plans_are_index_bounded(database):
    # Capture the actual two route statements, including the correlated label.
    await related(database)
    statements = database.statements.copy()
    engine = create_async_engine(URL)
    assert engine.url.host in {"127.0.0.1", "localhost"}
    assert engine.url.database.endswith("_read_refresh_verification")
    name = "node_incident_" + uuid4().hex
    admin = create_async_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    await engine.dispose()
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_async_engine(engine.url.set(database=name))
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
            for schema in ("graph", "ontology", "search"):
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.execute(text("""CREATE TABLE graph.node (
                id bigint PRIMARY KEY, type_id int, canonical_key text, slug text,
                created_release_id bigint, status text, created_at timestamp,
                updated_at timestamp, metadata jsonb)"""))
            await connection.execute(text("""CREATE TABLE graph.edge (
                id bigint PRIMARY KEY, from_node_id bigint, to_node_id bigint,
                relation_type_id int, layer text, basis text, confidence float,
                created_release_id bigint, valid_from_release_id bigint,
                valid_to_release_id bigint, processor_run_id bigint, metadata jsonb)"""))
            await connection.execute(text("CREATE TABLE graph.node_type (id int PRIMARY KEY, key text)"))
            await connection.execute(text("CREATE TABLE ontology.relation_type (id int PRIMARY KEY, key text)"))
            await connection.execute(text("CREATE TABLE search.document (id bigint PRIMARY KEY, target_node_id bigint, locale_id int, category text, title text)"))
            for endpoint in ("from_node_id", "to_node_id"):
                await connection.execute(text(f"CREATE INDEX ON graph.edge ({endpoint},relation_type_id)"))
            await connection.execute(text("CREATE INDEX ON search.document(target_node_id,locale_id,category)"))
            await connection.execute(text("INSERT INTO graph.node_type VALUES(1,'location'),(2,'source_reference')"))
            await connection.execute(text("INSERT INTO ontology.relation_type VALUES(1,'references')"))
            await connection.execute(text("""INSERT INTO graph.node(id,type_id,canonical_key,metadata)
                SELECT n,CASE WHEN n=1 THEN 1 ELSE 2 END,'node:'||n,'{"source_file":"A.json"}'::jsonb
                FROM generate_series(1,50000) n"""))
            await connection.execute(text("""INSERT INTO graph.edge(id,from_node_id,to_node_id,relation_type_id,layer)
                SELECT n,n+10,n+11,1,'source' FROM generate_series(1,49980) n"""))
            await connection.execute(text("""INSERT INTO graph.edge(id,from_node_id,to_node_id,relation_type_id,layer)
                VALUES(100001,1,2,1,'source'),(100002,3,1,1,'canonical'),(100003,1,1,1,'source')"""))
            await connection.execute(text("ANALYZE graph.node"))
            await connection.execute(text("ANALYZE graph.edge"))
            await connection.execute(text("ANALYZE graph.node_type"))
            await connection.execute(text("ANALYZE ontology.relation_type"))
            for statement in statements:
                sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
                plan = (await connection.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql))).scalar_one()[0]
                def walk(item):
                    yield item
                    for child in item.get("Plans", []):
                        yield from walk(child)
                steps = list(walk(plan["Plan"]))
                assert not [step for step in steps if step.get("Relation Name") in {"node", "edge"}
                            and step["Node Type"] == "Seq Scan"]
                assert any(step.get("Relation Name") == "node" and "Index" in step["Node Type"] for step in steps)
                assert any(step.get("CTE Name") == "incident_edges" and step["Actual Rows"] == 3 for step in steps)
                print(f"incident query: {plan['Execution Time']:.3f}ms, shared hits={plan['Plan']['Shared Hit Blocks']}")
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
