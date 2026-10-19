"""Optional integration check against a dedicated *_verification PostgreSQL database."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.db import models  # noqa: F401
from wuwa_story.db.base import SCHEMAS, Base
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.notices import GameNotice
from wuwa_story.db.models.search import SearchDocument
from wuwa_story.db.repositories.nodes import get_node, get_quest
from wuwa_story.ingestion import slug_backfill

URL = os.getenv("SLUG_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="Set SLUG_TEST_DATABASE_URL to an isolated PostgreSQL database")
@pytest.mark.asyncio
async def test_migration_new_imports_and_resumable_parallel_backfill(monkeypatch):
    engine = create_async_engine(URL)
    assert engine.url.database.endswith("_verification"), "Never run this setup against production"
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            ready = await connection.scalar(text("SELECT to_regprocedure('ops.public_slug(bigint,text)')"))
            if not ready:
                for schema in SCHEMAS:
                    await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                await connection.run_sync(Base.metadata.create_all)
                # Reproduce the schema before migration 0015.
                for table in ("game_event", "game_notice"):
                    await connection.execute(text(f"ALTER TABLE core.{table} DROP COLUMN slug"))
                await connection.execute(text("DROP INDEX graph.uq_node_type_slug"))
        if not ready:
            path = Path(__file__).parents[1] / "migrations/versions/0015_public_slugs.py"
            spec = importlib.util.spec_from_file_location("slug_migration", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            async with engine.connect() as connection:
                def upgrade(sync):
                    context = MigrationContext.configure(sync)
                    with context.begin_transaction(), Operations.context(context):
                        migration.upgrade()
                await connection.run_sync(upgrade)

        prefix = uuid4().hex
        async with factory() as session:
            for ident, code in ((1, "en"), (2, "uk")):
                if not await session.get(Locale, ident):
                    session.add(Locale(id=ident, code=code, name=code))
            for ident, kind in ((1, "item"), (2, "quest")):
                if not await session.get(NodeType, ident):
                    session.add(NodeType(id=ident, key=kind))
            await session.flush()
            nodes = [Node(type_id=1, canonical_key=f"item:{prefix}-{i}") for i in range(3)]
            quest_node = Node(type_id=2, canonical_key=f"quest:{prefix}")
            session.add_all([*nodes, quest_node])
            await session.flush()
            game_id = quest_node.id + 1_000_000
            session.add(Quest(node_id=quest_node.id, game_quest_id=game_id))
            for node, category in [*((node, "item") for node in nodes), (quest_node, "quest")]:
                session.add(SearchDocument(target_node_id=node.id, category=category, locale_id=1,
                                           title="Café <b>Astrite</b>", body="name", aliases=[], content_hash=b"x"))
            await session.commit()
            for node in [*nodes, quest_node]:
                await session.refresh(node)
                assert node.slug == f"{node.id}-cafe-astrite"
            first_slug = nodes[0].slug
            await session.execute(update(SearchDocument).where(SearchDocument.target_node_id == nodes[0].id).values(title="Renamed"))
            await session.commit()
            await session.refresh(nodes[0])
            assert nodes[0].slug == first_slug
            assert (await get_node(session, first_slug, ("item",))).id == nodes[0].id
            assert await get_node(session, first_slug, ("quest",)) is None
            assert (await get_quest(session, quest_node.slug)).game_quest_id == game_id
            assert (await get_quest(session, game_id)).node_id == quest_node.id
            await session.execute(update(Node).where(Node.id.in_([n.id for n in nodes])).values(slug=None))
            event = GameEvent(source=prefix, source_id="1", title="Endstate Matrix",
                              event_kind="recurring", source_url="https://example.test", source_revision="test", source_data={})
            notice = GameNotice(feed=prefix, locale="en", source_id="1", title="Matrix rules", category="game",
                                source_url="https://example.test", revision="a"*64, content={}, related_event_ids=[])
            session.add_all([event, notice])
            await session.commit()
            for row in (event, notice):
                await session.refresh(row)
                assert row.slug.startswith(f"{row.id}-")
            await session.execute(update(GameEvent).where(GameEvent.id == event.id).values(slug=None))
            await session.execute(update(GameNotice).where(GameNotice.id == notice.id).values(slug=None))
            await session.commit()

        monkeypatch.setattr(slug_backfill, "SessionFactory", factory)
        async with factory() as blocker:
            await blocker.execute(select(Node).where(Node.id == nodes[0].id).with_for_update())
            result = await slug_backfill.backfill(workers=2, batch_size=1, pause=0)
            assert result["nodes"] == 2  # Locked node is skipped, rather than timing out.
            assert result["events"] == 1 and result["notices"] == 1
            await blocker.rollback()
        result = await slug_backfill.backfill(workers=2, batch_size=1, pause=0)
        assert result["nodes"] == 1
        assert (await slug_backfill.backfill(dry_run=True))["nodes"] == 0
        async with factory() as session:
            assert await session.scalar(text("SELECT ops.public_slug(9223372036854775806,'Astrite')")) == "9223372036854775806-astrite"
            assert not (await session.scalar(text("SELECT ops.public_slug(5, repeat('a',179) || '-hello')"))).endswith('-')
    finally:
        await engine.dispose()
