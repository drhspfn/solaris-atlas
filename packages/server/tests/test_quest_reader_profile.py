"""Quest-reader projection stays compact without changing the default profile."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from wuwa_story.api import read_projections
from wuwa_story.api.routes.story import profiles
from wuwa_story.read_models.readers import entities

PROFILE = {
    "quest": {"canonical_key": "quest:test", "name": {"content": "A quest"}},
    "scenes": [{"id": 1}],
    "explicit_links": [{"relation": "requires_quest", "node": {"type": "quest"}}],
    "item_requirements": [{"item_id": 12}],
    "flow_states": [{"large": "unused" * 1000}],
    "tree": [{"large": "unused" * 1000}],
}


@pytest.mark.asyncio
async def test_quest_profile_view_preserves_default_and_routes_reader(monkeypatch):
    app = FastAPI()
    app.include_router(profiles.router)
    app.dependency_overrides[profiles.get_session] = lambda: SimpleNamespace()
    monkeypatch.setattr(read_projections, "should_route_to_projection", lambda *args, **kwargs: True)
    projected = AsyncMock(return_value=read_projections.ProjectedPayload(PROFILE))
    monkeypatch.setattr(read_projections, "profile_page", projected)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/quests/7/profile")).json() == PROFILE
        assert projected.await_args.kwargs == {}
        assert (await client.get("/quests/7/profile?view=reader")).status_code == 200
        assert projected.await_args.kwargs == {"reader": True}
        assert (await client.get("/quests/7/profile?view=unknown")).status_code == 422


@pytest.mark.asyncio
async def test_source_reader_keeps_only_required_fields_and_default_is_unchanged(monkeypatch):
    monkeypatch.setattr(read_projections, "should_route_to_projection", lambda *args, **kwargs: False)
    monkeypatch.setattr(profiles.usecases, "quest_profile", AsyncMock(return_value=PROFILE))
    full = await profiles.quest_profile.__wrapped__("7", session=SimpleNamespace())
    compact = await profiles.quest_profile.__wrapped__("7", session=SimpleNamespace(), view="reader")
    assert full == PROFILE
    assert compact == {key: PROFILE[key] for key in entities.QUEST_READER_PROFILE_FIELDS}
    assert len(json.dumps(compact)) < len(json.dumps(full)) // 2


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("WUWA_QUEST_READER_TEST_DATABASE_URL"),
    reason="Set WUWA_QUEST_READER_TEST_DATABASE_URL to an isolated local verification database",
)
async def test_reader_projection_on_postgres_preserves_fields_and_unready_state(monkeypatch):
    url = os.environ["WUWA_QUEST_READER_TEST_DATABASE_URL"]
    parsed = make_url(url)
    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.host in {"127.0.0.1", "localhost", "::1"}
    assert parsed.database.startswith("quest_") and parsed.database.endswith("_verification")
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                assert await conn.scalar(text("SELECT current_database()")) == parsed.database
                assert not await conn.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'core')"
                )), "Use an empty dedicated database"
                await conn.execute(text("CREATE SCHEMA core"))
                await conn.execute(text("""
                    CREATE TABLE core.quest_read (
                        entity_id bigint, locale_id smallint, snapshot_key text, canonical_key text,
                        slug text, generation bigint, payload jsonb
                    )
                """))
                for identity, profile in ((1, PROFILE), (2, {}), (3, None)):
                    await conn.execute(text("""
                        INSERT INTO core.quest_read VALUES
                        (:id, 1, 'current', :key, NULL, 1, CAST(:payload AS jsonb))
                    """), {"id": identity, "key": f"quest:test{identity}",
                           "payload": json.dumps({"profile": profile})})
                monkeypatch.setattr(entities, "_require_scope", AsyncMock(
                    return_value=SimpleNamespace(status="ready")
                ))
                monkeypatch.setattr(entities, "_locale_id", AsyncMock(return_value=1))
                async with AsyncSession(bind=conn) as session:
                    full = await entities.profile_page(session, "quest", "quest:test1")
                    compact = await entities.profile_page(session, "quest", "quest:test1", reader=True)
                    assert compact == {key: full.get(key) for key in entities.QUEST_READER_PROFILE_FIELDS}
                    for identity in (2, 3):
                        with pytest.raises(HTTPException) as error:
                            await entities.profile_page(session, "quest", f"quest:test{identity}", reader=True)
                        assert error.value.status_code == 503
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
