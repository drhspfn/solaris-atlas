"""Run only against an isolated, migrated PostgreSQL test database."""
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.api.cache import REVISION_SQL, STORY_MAP_REVISION_SQL, STORY_MAP_TABLES

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
PARAMS = {"tables": list(STORY_MAP_TABLES), "expected_count": len(STORY_MAP_TABLES)}
DEPENDENCIES = {
    "raw.source_file", "raw.source_record", "ops.game_release", "ops.import_run",
    "core.quest", "graph.node_revision", "graph.node", "i18n.locale",
    "i18n.localization_key", "i18n.localization_value", "i18n.localization_content",
}


def test_story_map_dependency_list():
    assert set(STORY_MAP_TABLES) == DEPENDENCIES


@pytest.mark.asyncio
@pytest.mark.skipif(not DATABASE_URL, reason="Isolated PostgreSQL test URL is required")
async def test_story_map_dependencies_invalidate_and_unrelated_media_does_not():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as db:
            for table in (*sorted(DEPENDENCIES), "storage.file_object"):
                transaction = await db.begin()
                before = await db.scalar(STORY_MAP_REVISION_SQL, PARAMS)
                global_before = await db.scalar(REVISION_SQL)
                assert before is not None
                # A zero-row DELETE still invokes the real statement trigger without
                # changing content; both the revision update and DDL below roll back.
                schema, name = table.split(".")
                await db.execute(text(f'DELETE FROM "{schema}"."{name}" WHERE false'))
                after = await db.scalar(STORY_MAP_REVISION_SQL, PARAMS)
                assert (after != before) is (table in DEPENDENCIES)
                assert await db.scalar(REVISION_SQL) != global_before
                await transaction.rollback()
                assert await db.scalar(STORY_MAP_REVISION_SQL, PARAMS) == before
                await db.rollback()  # end the implicit read transaction

            transaction = await db.begin()
            await db.execute(text("DELETE FROM ops.public_cache_revision WHERE table_name='raw.source_file'"))
            assert await db.scalar(STORY_MAP_REVISION_SQL, PARAMS) is None
            await transaction.rollback()

            transaction = await db.begin()
            await db.execute(text("ALTER TABLE raw.source_file DISABLE TRIGGER public_cache_changed"))
            assert await db.scalar(STORY_MAP_REVISION_SQL, PARAMS) is None
            await transaction.rollback()

            before = await db.scalar(STORY_MAP_REVISION_SQL, PARAMS)
            await db.rollback()
            async with engine.begin() as writer:
                await writer.execute(text("DELETE FROM storage.file_object WHERE false"))
            assert await db.scalar(STORY_MAP_REVISION_SQL, PARAMS) == before
            await db.rollback()
            async with engine.begin() as writer:
                await writer.execute(text("DELETE FROM raw.source_record WHERE false"))
            assert await db.scalar(STORY_MAP_REVISION_SQL, PARAMS) != before
    finally:
        await engine.dispose()
