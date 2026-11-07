import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from wuwa_story.admin.composition import install_admin
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.auth.security import hash_password, verify_password
from wuwa_story.auth.services import create_session
from wuwa_story.config.settings import Settings
from wuwa_story.db.models.agent_workspace import AgentMemory, AgentToolAudit
from wuwa_story.db.models.auth import AuthSession, User

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="Isolated PostgreSQL required")


@pytest.fixture(scope="module")
async def console_database():
    name = "console_test_" + uuid4().hex
    owner = create_async_engine(DATABASE_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    url = make_url(DATABASE_URL).set(database=name).render_as_string(hide_password=False)
    async with owner.connect() as connection:
        from sqlalchemy import text
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": url, "WUWA_ONLINE_SEARCH_INDEX_ONLY": "0", "WUWA_DB_TRANSACTION_POOLING": "0"},
            capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        yield url
    finally:
        async with owner.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await owner.dispose()


@pytest.fixture
async def console(monkeypatch, console_database):
    from wuwa_story.admin import views
    monkeypatch.setattr(views, "get_agent_settings", lambda: AgentSettings(
        _env_file=None, embedding_model=""))
    engine = create_async_engine(console_database, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, app_env="test",
                        auth_cookie_secure=False)
    suffix = uuid4().hex[:12]
    async with factory() as db:
        admin = User(email=f"admin-{suffix}@example.com", email_normalized=f"admin-{suffix}@example.com",
                     nickname="op" + suffix, nickname_normalized="op" + suffix, role=2, status=1,
                     password_hash=hash_password("isolated-password-123"))
        member = User(email=f"member-{suffix}@example.com", email_normalized=f"member-{suffix}@example.com",
                      nickname="u" + suffix, nickname_normalized="u" + suffix, role=1, status=1,
                      password_hash=admin.password_hash)
        db.add_all([admin, member])
        await db.flush()
        token = await create_session(db, admin, settings, None, "console-test")
        member_token = await create_session(db, member, settings, None, "console-test")
        await db.commit()
    app = FastAPI()
    panel = install_admin(app, engine, settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://console.test") as client:
        yield client, factory, settings, admin, member, token, member_token, suffix, panel
    async with factory() as db:
        await db.execute(delete(AgentMemory).where(AgentMemory.key.contains(suffix)))
        await db.execute(delete(User).where(User.email_normalized.contains(suffix)))
        await db.commit()
    await engine.dispose()


def authorize(console):
    client, _, settings, _, _, token, *_ = console
    client.cookies.set(settings.auth_cookie_name, token)
    return client


async def test_budget_usage_page_shows_limits_and_is_read_only(console):
    client = console[0]
    assert (await client.get("/console/usage/list")).status_code == 303
    authorize(console)
    response = await client.get("/console/usage/list")
    assert response.status_code == 200
    assert "Daily budget overview" in response.text
    assert "No separate cap" in response.text
    assert "Europe/Kyiv" in response.text
    assert "Reservations remain held" in response.text
    assert (await client.get("/console/usage/create")).status_code == 403


async def test_generated_console_requires_live_admin_and_hides_secrets(console):
    client, _, settings, admin, member, _, member_token, *_ = console
    assert (await client.get("/console/api/users")).status_code == 303
    client.cookies.set(settings.auth_cookie_name, member_token)
    assert (await client.get("/console/api/users")).status_code == 403
    authorize(console)
    response = await client.get("/console/api/users", params={"limit": 25})
    assert response.status_code == 200, response.text
    assert admin.email in response.text
    for secret in ("password_hash", "token_hash", admin.password_hash):
        assert secret not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    sessions = await client.get("/console/api/sessions")
    assert "token_hash" not in sessions.text
    assert (await client.get("/console/api/users?limit=10000")).status_code == 422


async def test_native_login_reuses_db_session_and_rejects_public_user(console):
    client, factory, settings, admin, member, *_ = console
    assert (await client.get("/console/login")).status_code == 200
    data = {"username": admin.email, "password": "isolated-password-123"}
    assert (await client.post("/console/login", data=data)).status_code == 403
    assert (await client.post("/console/login", data=data, headers={"Origin": "https://evil.test"})).status_code == 403
    denied = await client.post("/console/login", data={**data, "username": member.email}, headers={"Origin": "http://console.test"})
    assert denied.status_code in (400, 422)
    response = await client.post("/console/login", data=data, headers={"Origin": "http://console.test"})
    assert response.status_code == 303, response.text
    assert settings.auth_cookie_name in response.cookies
    assert "HttpOnly" in response.headers["set-cookie"]
    assert (await client.get("/console/users/list")).status_code == 200


async def test_native_orm_user_form_hashes_password_and_audits(console):
    client = authorize(console)
    _, factory, _, admin, _, _, _, suffix, _ = console
    response = await client.post("/console/users/create", data={
        "email": f"new-{suffix}@example.com", "nickname": "new" + suffix,
        "password": "new-password-123", "role": "1", "status": "1",
    }, headers={"Origin": "http://console.test"})
    assert response.status_code == 303, response.text[-2500:]
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email_normalized == f"new-{suffix}@example.com"))
        assert user is not None and verify_password(
            "new-password-123", user.password_hash)
        receipt = await db.scalar(select(AgentToolAudit).where(AgentToolAudit.tool == "console.user.create",
                                                               AgentToolAudit.arguments["actor_user_id"].as_integer() == admin.id))
        assert receipt and "password" not in str(receipt.arguments)


async def test_native_memory_form_uses_versioned_service(console):
    client = authorize(console)
    _, factory, _, _, _, _, _, suffix, _ = console
    response = await client.post("/console/memory/create", data={
        "key": "console-" + suffix, "scope": "long", "text": "A source-backed working note.",
        "citations": "[]", "run_id": "", "node_id": "",
    }, headers={"Origin": "http://console.test"})
    assert response.status_code == 303, response.text[-2500:]
    async with factory() as db:
        memory = await db.scalar(select(AgentMemory).where(AgentMemory.key == "long:console-" + suffix))
        assert memory and memory.revision == 1
        identity = memory.id
    data = {"text": "A revised note.", "citations": "[]",
            "node_id": "", "revision": "1"}
    first = await client.post(f"/console/memory/edit/{identity}", data=data, headers={"Origin": "http://console.test"})
    assert first.status_code == 303, first.text[-2500:]
    stale = await client.post(f"/console/memory/edit/{identity}", data=data, headers={"Origin": "http://console.test"})
    assert stale.status_code == 422


async def test_revocation_is_immediate_and_get_actions_require_same_origin(console):
    client = authorize(console)
    _, factory, _, admin, *_ = console
    async with factory() as db:
        identity = await db.scalar(select(AuthSession.id).where(AuthSession.user_id == admin.id))
    url = f"/console/api/sessions/row-action?name=revoke&pk={identity}"
    assert (await client.get(url)).status_code == 403
    result = await client.get(url, headers={"Referer": "http://console.test/console/sessions/list"})
    assert result.status_code == 200, result.text
    assert (await client.get("/console/api/users")).status_code == 303


async def test_execution_history_cannot_be_created_or_deleted(console):
    client = authorize(console)
    assert (await client.get("/console/runs/create")).status_code == 403
    denied = await client.get("/console/api/users/action?name=delete&pks=1",
                              headers={"Referer": "http://console.test/console/users/list"})
    assert denied.status_code == 400
    assert (await client.get("/console/api/jobs")).status_code == 200
    for view in console[-1]._models:
        assert not view.can_delete(None)


async def test_last_admin_protection_and_duplicate_form_recovery(console):
    client = authorize(console)
    _, factory, _, admin, _, _, _, suffix, _ = console
    data = {"email": admin.email, "nickname": admin.nickname, "role": "1", "status": "1", "password": ""}
    response = await client.post(f"/console/users/edit/{admin.id}", data=data,
        headers={"Origin": "http://console.test"})
    assert response.status_code == 422
    assert "at least one active administrator" in response.text
    async with factory() as db:
        assert (await db.get(User, admin.id)).role == 2
    duplicate = await client.post("/console/users/create", data={**data,
        "nickname": "duplicate" + suffix, "password": "password-duplicate-123"},
        headers={"Origin": "http://console.test"})
    assert duplicate.status_code == 422
    assert admin.password_hash not in duplicate.text


async def test_native_work_creation_and_account_disable_revoke_sessions(console):
    from wuwa_story.db.models.agent_workspace import AgentWorkItem
    from wuwa_story.db.models.core import Quest
    from wuwa_story.db.models.graph import Node, NodeType
    client = authorize(console)
    _, factory, _, _, member, _, _, suffix, _ = console
    async with factory() as db:
        node = Node(type_id=await db.scalar(select(NodeType.id).where(NodeType.key == "quest")),
            canonical_key="console-quest:" + suffix)
        db.add(node)
        await db.flush()
        db.add(Quest(node_id=node.id, game_quest_id=node.id))
        await db.commit()
        node_id = node.id
    try:
        result = await client.post("/console/work/create", data={"node_id": str(node_id),
            "title": "Analyze imported quest", "text": "Check the cross-quest question.", "priority": "1"},
            headers={"Origin": "http://console.test"})
        assert result.status_code == 303, result.text[-2500:]
        async with factory() as db:
            work = await db.scalar(select(AgentWorkItem).where(AgentWorkItem.node_id == node_id))
            assert work.kind == "analysis" and work.status == "open" and work.revision == 1
            db.add(AgentWorkItem(key="question:" + suffix, kind="question", node_id=node_id,
                priority=1, status="open", title="Unresolved crossing question", text="Find the later source.",
                revision=1, search_terms=[], citations=[], resolution={}))
            await db.commit()
        questions = await client.get("/console/api/open-questions")
        assert questions.status_code == 200
        assert "Unresolved crossing question" in questions.text
        assert "Analyze imported quest" not in questions.text
        disabled = await client.post(f"/console/users/edit/{member.id}", data={
            "email": member.email, "nickname": member.nickname, "role": "1", "status": "2", "password": ""},
            headers={"Origin": "http://console.test"})
        assert disabled.status_code == 303, disabled.text[-2500:]
        async with factory() as db:
            sessions = list(await db.scalars(select(AuthSession).where(AuthSession.user_id == member.id)))
            assert all(item.revoked_at is not None for item in sessions)
    finally:
        async with factory() as db:
            await db.execute(delete(Node).where(Node.id == node_id))
            await db.commit()


async def test_create_admin_cli_does_not_create_browser_session(console, monkeypatch, console_database):
    from wuwa_story.auth import cli
    _, factory, settings, _, _, _, _, suffix, _ = console
    monkeypatch.setattr(cli, "get_settings", lambda: settings.model_copy(update={"database_url": console_database}))
    await cli._create_admin(f"bootstrap-{suffix}@example.com", "boot" + suffix, "bootstrap-password-123")
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email_normalized == f"bootstrap-{suffix}@example.com"))
        assert user.role == 2 and verify_password("bootstrap-password-123", user.password_hash)
        assert not await db.scalar(select(AuthSession.id).where(AuthSession.user_id == user.id))


async def test_console_forms_work_behind_https_reverse_proxy(console):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    client, _, _, admin, *_ = console
    wrapped = ProxyHeadersMiddleware(client._transport.app, trusted_hosts="127.0.0.1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(wrapped, client=("127.0.0.1", 123)),
                                base_url="http://console.test") as proxy:
        response = await proxy.post("/console/login", data={"username": admin.email, "password": "isolated-password-123"},
            headers={"Origin": "https://console.test", "X-Forwarded-Proto": "https"})
        assert response.status_code == 303, response.text
        assert response.headers["location"].startswith("https://console.test/console/")

async def test_alerts_filter_details_and_resolve_are_protected(console):
    from wuwa_story.db.models.agents import AgentNote
    from wuwa_story.db.models.graph import Node, NodeType
    from wuwa_story.db.models.i18n import Locale
    from wuwa_story.db.models.ops import GameRelease, ProcessingRun, Processor

    client, factory, settings, admin, _, _, member_token, suffix, _ = console
    async with factory() as db:
        node = Node(type_id=await db.scalar(select(NodeType.id).where(NodeType.key == "quest")),
                    canonical_key="console-alert:" + suffix)
        release = GameRelease(sequence=1900000000, game_version="console-" + suffix, upstream_name="test")
        processor = Processor(key="console-alert-" + suffix, version="1")
        locale = Locale(id=30000, code="test-" + suffix[:8], name="Test")
        db.add_all([node, release, processor, locale])
        await db.flush()
        run = ProcessingRun(processor_id=processor.id)
        db.add(run)
        await db.flush()
        values = dict(run_id=run.id, node_id=node.id, release_id=release.id, locale_id=locale.id,
                      citations=[{"node_id": node.id, "quote": "A source citation", "locale": "en"}])
        alert = AgentNote(kind="missing_source", status="open", text="Open alert text", **values)
        ordinary = AgentNote(kind="note", status="open", text="Ordinary research note", **values)
        closed = AgentNote(kind="missing_source", status="resolved", text="Already resolved alert", **values)
        db.add_all([alert, ordinary, closed])
        await db.commit()
        alert_id, note_id, run_id = alert.id, ordinary.id, run.id
    assert (await client.get('/console/api/alerts')).status_code == 303
    client.cookies.set(settings.auth_cookie_name, member_token)
    assert (await client.get('/console/api/alerts')).status_code == 403
    authorize(console)
    listing = await client.get('/console/api/alerts')
    assert listing.status_code == 200, listing.text
    assert 'Open alert text' in listing.text
    assert 'Ordinary research note' not in listing.text and 'Already resolved alert' not in listing.text
    detail = await client.get(f'/console/alerts/detail/{alert_id}')
    assert detail.status_code == 200, detail.text
    assert 'A source citation' in detail.text
    assert f'/console/runs/detail/{run_id}' in detail.text
    url = f'/console/api/alerts/row-action?name=resolve&pk={alert_id}'
    assert (await client.get(url)).status_code == 403
    assert (await client.get(url, headers={'Referer': 'http://console.test/console/alerts/list'})).status_code == 200
    assert (await client.get(url, headers={'Referer': 'http://console.test/console/alerts/list'})).status_code == 400
    assert (await client.get(f'/console/api/alerts/row-action?name=resolve&pk={note_id}',
                             headers={'Referer': 'http://console.test/console/alerts/list'})).status_code == 400
    async with factory() as db:
        assert (await db.get(AgentNote, alert_id)).status == 'resolved'
        assert (await db.get(AgentNote, note_id)).status == 'open'
        receipts = list(await db.scalars(select(AgentToolAudit).where(AgentToolAudit.tool == 'console.alert.resolve',
                       AgentToolAudit.arguments['actor_user_id'].as_integer() == admin.id)))
        assert len(receipts) == 1 and receipts[0].arguments['object_id'] == alert_id
