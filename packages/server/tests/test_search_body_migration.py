"""Check concurrent body index migration and interrupted-build recovery."""

import importlib.util
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

SOURCE = Path(__file__).resolve().parents[1] / "migrations/versions/0023_search_body_trgm.py"


def migration():
    spec = importlib.util.spec_from_file_location("search_body_migration", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("valid", [None, False, True])
def test_repairs_only_invalid_index_and_creates_concurrently(monkeypatch, valid):
    module = migration()
    operations = SimpleNamespace(
        get_context=lambda: SimpleNamespace(autocommit_block=nullcontext),
        get_bind=lambda: SimpleNamespace(scalar=Mock(return_value=valid)),
        execute=Mock(), create_index=Mock(), drop_index=Mock(),
    )
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    assert operations.execute.call_count == int(valid is False)
    if valid is False:
        operations.execute.assert_called_once_with(
            "DROP INDEX CONCURRENTLY search.ix_search_document_body_trgm"
        )
    operations.create_index.assert_called_once_with(
        module.INDEX, "document", ["body"], schema="search", postgresql_using="gin",
        postgresql_ops={"body": "gin_trgm_ops"}, postgresql_concurrently=True, if_not_exists=True,
    )
    module.downgrade()
    operations.drop_index.assert_called_once_with(
        module.INDEX, table_name="document", schema="search", postgresql_concurrently=True,
        if_exists=True,
    )


def test_orm_index_matches_migration():
    from wuwa_story.db.models.search import SearchDocument

    index = next(i for i in SearchDocument.__table__.indexes if i.name == "ix_search_document_body_trgm")
    assert [column.name for column in index.columns] == ["body"]
    assert index.dialect_options["postgresql"]["using"] == "gin"
    assert index.dialect_options["postgresql"]["ops"] == {"body": "gin_trgm_ops"}


@pytest.mark.skipif(not os.getenv("SEARCH_INDEX_TEST_DATABASE_URL"), reason="Requires an isolated PostgreSQL database")
async def test_postgres_invalid_build_recovery_and_roundtrip():
    engine = create_async_engine(os.environ["SEARCH_INDEX_TEST_DATABASE_URL"])
    assert engine.url.database.endswith("_verification"), "Use a dedicated verification database"
    created_schema = False
    try:
        async with engine.begin() as connection:
            if not await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm')")):
                pytest.skip("Verification database needs pg_trgm")
            if await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='search')")):
                pytest.skip("Verification database must not contain a search schema")
            await connection.execute(text("CREATE SCHEMA search"))
            created_schema = True
            await connection.execute(text("CREATE TABLE search.document (id integer PRIMARY KEY, title text, body text)"))
            await connection.execute(text("INSERT INTO search.document VALUES (1, 'core', 'sword'), (2, 'other', 'sword')"))
            before = (await connection.execute(text("SELECT id FROM search.document WHERE title % 'sword' OR body % 'sword' ORDER BY id"))).all()

        # A failed concurrent unique build leaves a real invalid index behind.
        async with engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            with pytest.raises(DBAPIError):
                await connection.execute(text("CREATE UNIQUE INDEX CONCURRENTLY ix_search_document_body_trgm ON search.document (body)"))

        module = migration()

        def run(sync, action):
            context = MigrationContext.configure(sync)
            with context.begin_transaction(), Operations.context(context):
                action()

        async with engine.connect() as connection:
            await connection.run_sync(run, module.upgrade)
        async with engine.connect() as connection:
            index = (await connection.execute(text("""
                SELECT c.oid, i.indisvalid, pg_get_indexdef(c.oid)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_index i ON i.indexrelid=c.oid
                WHERE n.nspname='search' AND c.relname='ix_search_document_body_trgm'
            """))).one()
            assert index.indisvalid and "gin_trgm_ops" in index.pg_get_indexdef
            after = (await connection.execute(text("SELECT id FROM search.document WHERE title % 'sword' OR body % 'sword' ORDER BY id"))).all()
            assert after == before
        async with engine.connect() as connection:
            await connection.run_sync(run, module.upgrade)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 'search.ix_search_document_body_trgm'::regclass::oid")) == index.oid
        async with engine.connect() as connection:
            await connection.run_sync(run, module.downgrade)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT to_regclass('search.ix_search_document_body_trgm')")) is None
    finally:
        if created_schema:
            async with engine.begin() as connection:
                await connection.execute(text("DROP SCHEMA search CASCADE"))
        await engine.dispose()
