import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from wuwa_story.api.routes import visual_memory
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.session import get_session


@pytest.mark.asyncio
async def test_hsin_search_uses_character_and_npc_catalog(monkeypatch):
    from wuwa_story.api.routes import search

    api = FastAPI()
    api.include_router(visual_memory.router)
    api.dependency_overrides[get_session] = lambda: None
    api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=7)
    search_mock = AsyncMock(return_value={"results": [{"id": 123, "title": "Hsin",
        "node_type": "character", "character_kind": "npc", "canonical_key": "character:npc:123",
        "image_url": "https://media.example/hsin.png"}]})
    monkeypatch.setattr(search, "search", search_mock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        result = await client.get("/admin/visual-memory/game-entities?q=Hsin")
        assert result.status_code == 200
        assert result.json()["items"] == [{"node_id": 123, "name": "Hsin", "kind": "npc",
            "canonical_key": "character:npc:123", "image_url": "https://media.example/hsin.png"}]
        assert result.json()["total"] == 1
        assert search_mock.await_args.kwargs["character_kind"] == "all"
        api.dependency_overrides[require_admin] = lambda: (_ for _ in ()).throw(HTTPException(403, "Forbidden"))
        assert (await client.get("/admin/visual-memory/game-entities?q=Hsin")).status_code == 403
        assert (await client.post("/admin/visual-memory/identities/from-game", json={"node_id": 123})).status_code == 403


@pytest.mark.asyncio
async def test_create_from_game_and_reuse_on_disposable_postgres(monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from wuwa_story.db.models.core import Character
    from wuwa_story.db.models.graph import Node, NodeType
    from wuwa_story.db.models.i18n import Locale
    from wuwa_story.db.models.search import EntityAlias
    from wuwa_story.db.models.visual_memory import VisualIdentity, VisualIdentityNode
    from wuwa_story.story import service as story_service

    url = os.getenv("WUWA_VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WUWA_VISUAL_TEST_DATABASE_URL to a disposable migrated database")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(connection, expire_on_commit=False, autoflush=False,
                                join_transaction_mode="create_savepoint") as session:
            node_type = await session.scalar(select(NodeType).where(NodeType.key == "character"))
            node = Node(type_id=node_type.id, canonical_key="character:npc:visual-game-test")
            session.add(node)
            await session.flush()
            session.add(Character(node_id=node.id, canonical_name="Hsin", metadata_json={"role_type": 6}))
            locale = await session.scalar(select(Locale).where(Locale.code == "en"))
            session.add(EntityAlias(node_id=node.id, locale_id=locale.id, alias="Hsin",
                                    normalized_alias="hsin", alias_type="name", source="test"))
            await session.flush()
            api = FastAPI()
            api.include_router(visual_memory.router)
            api.dependency_overrides[get_session] = lambda: session
            api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=7)
            api.dependency_overrides[require_csrf] = lambda: None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
                url = "/admin/visual-memory/identities/from-game"
                found = await client.get("/admin/visual-memory/game-entities?q=Hsin")
                assert found.status_code == 200
                assert [(x["node_id"], x["name"], x["kind"]) for x in found.json()["items"]] == [(node.id, "Hsin", "npc")]
                monkeypatch.setattr(story_service, "_node_label", AsyncMock(return_value={"label": "Hsin"}))
                assert (await client.post(url, json={"node_id": 999999999})).status_code == 404
                first = await client.post(url, json={"node_id": node.id})
                assert first.status_code == 200
                assert first.json()["name"] == "Hsin" and first.json()["kind"] == "npc"
                second = await client.post(url, json={"node_id": node.id})
                assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
                other = VisualIdentity(name="Other", kind="npc")
                session.add(other)
                await session.flush()
                session.add(VisualIdentityNode(identity_id=other.id, node_id=node.id))
                await session.flush()
                assert (await client.post(url, json={"node_id": node.id})).status_code == 422
        await transaction.rollback()
    await engine.dispose()
