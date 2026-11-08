"""Real PostgreSQL regressions; use only an isolated migrated test database."""

import asyncio
import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.db.models import (
    EmbeddingModel,
    User,
    VisualIdentity,
    VisualReference,
    VisualReferenceLink,
    VisualReview,
)
from wuwa_story.storage.local import LocalStorage
from wuwa_story.visual_memory.images import encode_png
from wuwa_story.visual_memory.service import VisualMemoryService
from wuwa_story.visual_memory.tools import VisualMemoryTools


@pytest.mark.asyncio
async def test_quality_filter_validation_and_forwarding(monkeypatch):
    from wuwa_story.api.routes import visual_memory
    from wuwa_story.auth.dependencies import require_admin
    from wuwa_story.db.session import get_session

    app = FastAPI()
    app.include_router(visual_memory.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_session] = lambda: object()
    service = SimpleNamespace(list_references=AsyncMock(return_value={"items": [], "total": 0}))
    monkeypatch.setattr(visual_memory, "service_for", lambda _: service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/admin/visual-memory/references?quality_min=0.75")).status_code == 200
        assert service.list_references.await_args.kwargs["quality_min"] == 0.75
        assert (await client.get("/admin/visual-memory/references?quality_min=1.1")).status_code == 422
        assert (await client.get("/admin/visual-memory/references?quality_min=nan")).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("forged_name", ["visual_create_identity", "visual_add_reference"])
async def test_internal_population_capabilities_and_public_spoof_rejected(monkeypatch, forged_name):
    from wuwa_story.agents import audit, chat
    from wuwa_story.agents.capabilities import allowed, execute_internal, internal_definitions
    from wuwa_story.agents.settings import AgentSettings
    from wuwa_story.visual_memory import tools

    names = {item["name"] for item in internal_definitions()}
    assert {"visual_create_identity", "visual_add_reference"} <= names
    assert not allowed("public", "visual_create_identity")
    assert not allowed("public", "visual_add_reference")
    service = SimpleNamespace(create_identity=AsyncMock(return_value={"id": 5}),
                              add_reference=AsyncMock(return_value={"status": "pending"}))
    monkeypatch.setattr(tools, "service_for", lambda _: service)
    await execute_internal(None, AgentSettings(_env_file=None), "visual_create_identity", {"name": "Unknown NPC", "kind": "npc"})
    service.create_identity.assert_awaited_once()
    await execute_internal(None, AgentSettings(_env_file=None), "visual_add_reference", {
        "image_base64": "eA==", "links": [{"identity_id": 5}]})
    assert service.add_reference.await_args.kwargs["source_type"] == "agent_observation"
    assert service.add_reference.await_args.kwargs["status"] == "pending"
    assert service.add_reference.await_args.kwargs["actor_id"] is None
    assert audit.redacted({"image_base64": "private"})["image_base64"] == "[redacted]"

    monkeypatch.setattr(chat, "get_agent_settings", lambda: AgentSettings(_env_file=None))
    monkeypatch.setattr(audit, "start", AsyncMock(return_value=1))
    monkeypatch.setattr(audit, "complete", AsyncMock())
    monkeypatch.setattr(chat, "execute_internal", AsyncMock())
    forged = SimpleNamespace(id="forged", name=forged_name, arguments={"name": "fake", "kind": "npc"})
    monkeypatch.setattr(chat, "call_model", AsyncMock(side_effect=[
        SimpleNamespace(calls=[forged], items=[], text=""),
        SimpleNamespace(calls=[], items=[], text="Created"),
    ]))
    await chat.run_chat(AsyncMock(), [SimpleNamespace(role="user", content="Create identity")], role="public")
    chat.execute_internal.assert_not_awaited()
    assert audit.complete.call_args.args[-1] == "error"


class ControlledEngine:
    def __init__(self, key):
        self.key = key

    def embed(self, _image):
        return SimpleNamespace(model_key=self.key, vector=[1.0] + [0.0] * 383,
                               config={"model_name": "test", "weights_sha256": self.key})


@pytest_asyncio.fixture
async def pg_memory(tmp_path):
    url = os.getenv("WUWA_VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WUWA_VISUAL_TEST_DATABASE_URL to an isolated migrated PostgreSQL database")
    db = create_async_engine(url)
    token = uuid4().hex
    model_key = "readonly-test-" + token
    image = encode_png(Image.frombytes("RGB", (16, 16), (bytes.fromhex(token) * 48)[:768]))
    identity_id = user_id = None
    try:
        async with AsyncSession(db, expire_on_commit=False, autoflush=False) as session:
            user = User(email=f"{token}@example.invalid", email_normalized=f"{token}@example.invalid",
                        nickname=token, nickname_normalized=token, password_hash="unused", role=2)
            session.add(user)
            await session.flush()
            user_id = user.id
            service = VisualMemoryService(session, LocalStorage(tmp_path), ControlledEngine(model_key))
            identity = await service.create_identity("readonly " + token, "npc")
            identity_id = identity["id"]
            ref = await service.add_reference(image, links=[{"identity_id": identity_id}],
                                              status="approved", actor_id=user_id,
                                              retention_mode="vector_only")
            await session.commit()
        yield SimpleNamespace(db=db, model_key=model_key, image=image, identity_id=identity_id,
                              reference_id=ref["id"], actor_id=user_id, storage=LocalStorage(tmp_path))
    finally:
        # Only delete rows belonging to this test's unique identity and model.
        async with AsyncSession(db) as session:
            if identity_id is not None:
                ref_ids = select(VisualReferenceLink.reference_id).where(
                    VisualReferenceLink.identity_id == identity_id)
                await session.execute(delete(VisualReview).where(VisualReview.reference_id.in_(ref_ids)))
                await session.execute(delete(VisualReference).where(VisualReference.id.in_(ref_ids)))
                await session.execute(delete(VisualIdentity).where(VisualIdentity.id == identity_id))
            await session.execute(delete(EmbeddingModel).where(EmbeddingModel.key == model_key))
            if user_id is not None:
                await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await db.dispose()


@pytest.mark.asyncio
async def test_registered_and_unknown_model_search_in_readonly_transaction(pg_memory):
    fixture = pg_memory
    unknown_key = fixture.model_key + "-unknown"
    async with AsyncSession(fixture.db, autoflush=False) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        assert await session.scalar(text("SHOW transaction_read_only")) == "on"
        service = VisualMemoryService(session, fixture.storage, ControlledEngine(fixture.model_key))
        found = await service.search(fixture.image)
        assert any(candidate["identity_id"] == fixture.identity_id for candidate in found["candidates"])
        service.engine = ControlledEngine(unknown_key)
        result = await service.search(fixture.image)
        assert result["model_key"] == unknown_key
        assert result["status"] == "unknown" and result["candidates"] == []
        assert await session.scalar(select(func.count()).select_from(EmbeddingModel).where(
            EmbeddingModel.key == unknown_key)) == 0
        await session.rollback()
    async with AsyncSession(fixture.db) as session:
        assert await session.scalar(select(func.count()).select_from(EmbeddingModel).where(
            EmbeddingModel.key == unknown_key)) == 0


@pytest.mark.asyncio
async def test_agent_writes_remain_pending_without_moderation(pg_memory):
    fixture = pg_memory
    async with AsyncSession(fixture.db, autoflush=False) as session:
        service = VisualMemoryService(session, fixture.storage, ControlledEngine(fixture.model_key))
        agent = VisualMemoryTools(service, allow_write=True, agent_source=True)
        image = encode_png(Image.new("RGB", (17, 13), (10, 50, 90)))
        args = {"image_base64": base64.b64encode(image).decode(),
                "links": [{"identity_id": fixture.identity_id}], "retention_mode": "vector_only"}
        with pytest.raises(PermissionError):
            await agent.execute("visual_add_reference", {**args, "status": "approved"})
        with pytest.raises(PermissionError):
            await agent.execute("visual_review_reference", {"reference_id": fixture.reference_id, "status": "approved"})
        pending = await agent.execute("visual_add_reference", args)
        assert pending["status"] == "pending" and pending["source_type"] == "agent_observation"
        assert not pending["enabled_for_search"]
        await session.rollback()


@pytest.mark.asyncio
async def test_concurrent_identical_uploads_are_deduplicated(pg_memory):
    fixture = pg_memory
    image = encode_png(Image.frombytes("RGB", (17, 16), (bytes.fromhex(uuid4().hex) * 51)[:816]))
    async with AsyncSession(fixture.db, expire_on_commit=False, autoflush=False) as first, \
            AsyncSession(fixture.db, expire_on_commit=False, autoflush=False) as second:
        kwargs = {"links": [{"identity_id": fixture.identity_id}], "retention_mode": "vector_only"}
        one = await VisualMemoryService(first, fixture.storage, ControlledEngine(fixture.model_key)).add_reference(image, **kwargs)
        pid = await second.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(VisualMemoryService(second, fixture.storage, ControlledEngine(fixture.model_key)).add_reference(image, **kwargs))
        try:
            async with AsyncSession(fixture.db) as observer:
                async with asyncio.timeout(10):
                    while not await observer.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = :pid AND NOT granted)"), {"pid": pid}):
                        await asyncio.sleep(0.02)
            await first.commit()
            two = await asyncio.wait_for(task, timeout=10)
            await second.commit()
            assert two["duplicate"] and two["id"] == one["id"]
            async with AsyncSession(fixture.db) as check:
                assert await check.scalar(select(func.count()).select_from(VisualReference).where(VisualReference.id == one["id"])) == 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
