import hashlib
import importlib.util
import os
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.db.models.i18n import LocalizationContent, LocalizationValue
from wuwa_story.ingestion.localization import import_localization_batch

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WUWA_TEST_DATABASE_URL is not configured")


def rows(content="same text"):
    return [
        {
            "key": "dedup-test",
            "values_by_locale": {
                "en": {"resolution": "resolved_nonempty", "content": content},
                "ja": {"resolution": "resolved_nonempty", "content": content},
                "fr": {"resolution": "resolved_empty", "content": ""},
                "de": {"resolution": "broken_redirect", "content": None},
                "ko": {"resolution": "missing_key"},
            },
        }
    ]


@pytest.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        # Isolated test database; every fixture rolls back its writes and DDL.
        release_ids = (
            (
                await connection.execute(
                    text("""
            INSERT INTO ops.game_release (sequence, game_version, upstream_name)
            VALUES (900001, 'dedup-test-1', 'test'), (900002, 'dedup-test-2', 'test')
            RETURNING id
        """)
                )
            )
            .scalars()
            .all()
        )
        async with AsyncSession(bind=connection) as value:
            yield value, release_ids
        await transaction.rollback()
    await engine.dispose()


async def test_releases_and_languages_share_text_without_losing_snapshot_state(session):
    db, (first, second) = session
    await import_localization_batch(db, first, rows())
    await import_localization_batch(db, second, rows())
    await import_localization_batch(db, first, rows())
    assert await db.scalar(select(func.count()).select_from(LocalizationContent)) == 2
    assert await db.scalar(select(func.count()).select_from(LocalizationValue)) == 8
    values = (await db.scalars(select(LocalizationValue))).all()
    nonempty = [value for value in values if value.status == "resolved_nonempty"]
    assert len({value.content_id for value in nonempty}) == 1
    assert all(value.content == "same text" for value in nonempty)
    assert all(value.content_hash == hashlib.sha256(b"same text").digest() for value in nonempty)
    assert all(value.content == "" for value in values if value.status == "resolved_empty")
    assert all(
        value.content_id is None and value.content is None
        for value in values
        if value.status == "broken_redirect"
    )
    filtered = (
        await db.scalars(
            select(LocalizationValue).where(
                LocalizationValue.content.ilike("%same%"), LocalizationValue.release_id == second
            )
        )
    ).all()
    assert len(filtered) == 2


async def test_changed_text_and_whitespace_remain_distinct(session):
    db, (first, second) = session
    await import_localization_batch(db, first, rows("same text"))
    await import_localization_batch(db, second, rows("same text "))
    assert await db.scalar(select(func.count()).select_from(LocalizationContent)) == 3
    assert set(
        (
            await db.scalars(
                select(LocalizationValue.content).where(
                    LocalizationValue.status == "resolved_nonempty"
                )
            )
        ).all()
    ) == {"same text", "same text "}


async def test_hash_collision_is_rejected(session):
    db, (first, _) = session
    db.add(
        LocalizationContent(
            content_hash=hashlib.sha256(b"same text").digest(), content="corrupt text"
        )
    )
    await db.flush()
    with pytest.raises(ValueError, match="collision or corrupt"):
        await import_localization_batch(db, first, rows())
    assert await db.scalar(select(func.count()).select_from(LocalizationValue)) == 0


def migrate(connection, direction):
    path = Path(__file__).parents[1] / "migrations/versions/0007_localization_content.py"
    spec = importlib.util.spec_from_file_location("localization_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


async def test_existing_data_migration_and_rollback_preserve_exact_values(session):
    db, (first, second) = session
    await import_localization_batch(db, first, rows())
    await import_localization_batch(db, second, rows("new text"))
    connection = await db.connection()
    await connection.run_sync(lambda conn: migrate(conn, "downgrade"))
    original = (
        await db.execute(
            text("""
        SELECT release_id, key_id, locale_id, content, content_hash, status,
               redirect_key_id, source_record_id, updated_at
        FROM i18n.localization_value ORDER BY release_id, key_id, locale_id
    """)
        )
    ).all()
    await connection.run_sync(lambda conn: migrate(conn, "upgrade"))
    assert await db.scalar(select(func.count()).select_from(LocalizationContent)) == 3
    await connection.run_sync(lambda conn: migrate(conn, "downgrade"))
    restored = (
        await db.execute(
            text("""
        SELECT release_id, key_id, locale_id, content, content_hash, status,
               redirect_key_id, source_record_id, updated_at
        FROM i18n.localization_value ORDER BY release_id, key_id, locale_id
    """)
        )
    ).all()
    assert restored == original
