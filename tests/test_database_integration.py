import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_DATABASE_URL, reason="WUWA_TEST_DATABASE_URL is not configured")
async def test_migrated_database_has_required_schemas_and_extensions() -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            schemas = set(
                (
                    await connection.execute(
                        text("SELECT schema_name FROM information_schema.schemata")
                    )
                ).scalars()
            )
            extensions = set(
                (await connection.execute(text("SELECT extname FROM pg_extension"))).scalars()
            )
        assert {
            "ops",
            "storage",
            "raw",
            "i18n",
            "graph",
            "core",
            "ontology",
            "story",
            "content",
            "search",
        } <= schemas
        assert {"vector", "pg_trgm", "pgcrypto"} <= extensions
    finally:
        await engine.dispose()
