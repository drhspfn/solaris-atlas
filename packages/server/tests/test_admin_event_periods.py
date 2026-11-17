from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.datastructures import FormData
from starlette_admin import RequestAction
from starlette_admin.exceptions import FormValidationError

from wuwa_story.admin.event_periods import EventPeriods, UTCDateTimeField
from wuwa_story.auth.constants import UserRole
from wuwa_story.config.settings import Settings
from wuwa_story.db.models.game_events import GameEventOccurrence


@pytest.mark.asyncio
async def test_period_dates_use_utc_and_keep_unknown_values():
    field = UTCDateTimeField("starts_at")
    def parse(raw):
        return field.parse_form_data(None, FormData({"starts_at": raw}), RequestAction.CREATE)
    assert await parse("") is None
    assert await parse("1900-01-02T03:04") == datetime(1900, 1, 2, 3, 4, tzinfo=UTC)
    assert await parse("2026-11-01T08:00:00+02:00") == datetime(2026, 11, 1, 6, tzinfo=UTC)
    assert await field.serialize_value(None, datetime(2026, 11, 1, 8, tzinfo=UTC), RequestAction.EDIT) == "2026-11-01T08:00:00"
    with pytest.raises(FormValidationError):
        await parse("bad date")


@pytest.mark.asyncio
async def test_period_validation_requires_existing_event_and_strict_order():
    db = SimpleNamespace(get=AsyncMock(return_value=object()))
    request = SimpleNamespace(state=SimpleNamespace(session=db))
    view = EventPeriods(GameEventOccurrence, identity="event-periods")
    start = datetime(1900, 1, 1, tzinfo=UTC)
    await view.validate(request, {"event_id": 1, "server": "europe", "starts_at": None, "ends_at": start})
    await view.validate(request, {"starts_at": start, "ends_at": start + timedelta(days=1)})
    for end in (start, start - timedelta(seconds=1)):
        with pytest.raises(FormValidationError):
            await view.validate(request, {"starts_at": start, "ends_at": end})
    db.get.return_value = None
    with pytest.raises(FormValidationError):
        await view.validate(request, {"event_id": 999, "server": "europe"})
    with pytest.raises(FormValidationError):
        await view.validate(request, {"event_id": None, "server": "europe"})


@pytest.mark.asyncio
async def test_create_produces_independent_ids_and_audit(monkeypatch):
    db = SimpleNamespace(flush=AsyncMock(), add=Mock())
    request = SimpleNamespace(state=SimpleNamespace(session=db, console_user=SimpleNamespace(id=12)))
    view = EventPeriods(GameEventOccurrence, identity="event-periods")
    rows = [GameEventOccurrence(id=i, event_id=7, server="asia", starts_at=None) for i in (1, 2)]
    for row in rows:
        await view.before_create(request, {}, row)
    assert len({row.source_occurrence_id for row in rows}) == 2
    assert all(row.source_occurrence_id.startswith("manual:") and row.source_data == {"schedule_source": "manual"}
               and row.event_id == 7 and row.starts_at is None for row in rows)
    assert db.add.call_count == 2
    assert all(call.args[0].tool == "console.event_period.create" for call in db.add.call_args_list)


@pytest.mark.asyncio
async def test_edit_locks_and_preserves_concurrent_provenance():
    db = SimpleNamespace(no_autoflush=nullcontext(), scalar=AsyncMock(return_value={
        "client": {"download_id": "current"}, "notice_id": 10, "start_estimate": {"basis": "old"},
    }), add=Mock())
    request = SimpleNamespace(state=SimpleNamespace(session=db, console_user=SimpleNamespace(id=12)))
    row = GameEventOccurrence(id=1, event_id=7, server="asia", starts_at=None, ends_at=None,
                              game_version="3.8", source_data={})
    await EventPeriods(GameEventOccurrence, identity="event-periods").before_edit(request, {}, row)
    assert row.source_data == {"client": {"download_id": "current"}, "notice_id": 10,
                               "schedule_source": "manual"}
    assert "FOR UPDATE" in str(db.scalar.await_args.args[0])
    assert db.add.call_args.args[0].tool == "console.event_period.edit"


@pytest.mark.asyncio
async def test_period_console_requires_admin_and_same_origin(monkeypatch):
    from wuwa_story.admin import auth
    from wuwa_story.admin.composition import install_admin

    async def identity(session, token):
        if not token:
            return None
        user = SimpleNamespace(id=1, nickname="test", role=UserRole.ADMIN if token == "admin" else UserRole.USER)
        return user, SimpleNamespace(id=2)

    monkeypatch.setattr(auth, "get_user_for_token", identity)
    settings = Settings(_env_file=None, app_env="test", auth_cookie_secure=False)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    app = FastAPI()
    install_admin(app, engine, settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://console.test") as client:
        assert (await client.get("/console/event-periods/create")).status_code == 303
        client.cookies.set(settings.auth_cookie_name, "member")
        assert (await client.get("/console/event-periods/create")).status_code == 403
        client.cookies.set(settings.auth_cookie_name, "admin")
        assert (await client.get("/console/event-periods/create")).status_code == 200
        assert (await client.post("/console/event-periods/create", data={})).status_code == 403
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_create_period_writes_existing_event_and_audit(monkeypatch):
    from wuwa_story.admin import auth
    from wuwa_story.admin.composition import install_admin

    monkeypatch.setattr(auth, "get_user_for_token", AsyncMock(return_value=(
        SimpleNamespace(id=1, nickname="test", role=UserRole.ADMIN), SimpleNamespace(id=2))))
    settings = Settings(_env_file=None, app_env="test", auth_cookie_secure=False)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for sql in (
            "ATTACH DATABASE ':memory:' AS core", "ATTACH DATABASE ':memory:' AS ops",
            "CREATE TABLE core.game_event (id INTEGER PRIMARY KEY, source TEXT, source_id TEXT, title TEXT, slug TEXT, description TEXT, event_kind TEXT, group_label_key TEXT, game_path TEXT, banner_path TEXT, rewards JSON, source_url TEXT, source_revision TEXT, source_data JSON, updated_at DATETIME)",
            "CREATE TABLE core.game_event_occurrence (id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, source_occurrence_id TEXT NOT NULL, game_version TEXT, server TEXT NOT NULL, starts_at DATETIME, ends_at DATETIME, banner_path TEXT, rewards JSON, season JSON, source_data JSON NOT NULL)",
            "CREATE TABLE ops.agent_tool_audit (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, role TEXT NOT NULL, run_id INTEGER, step INTEGER, call_id TEXT NOT NULL, tool TEXT NOT NULL, status TEXT NOT NULL, arguments JSON NOT NULL, result JSON NOT NULL, duration_ms INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
            "INSERT INTO core.game_event (id,title,source_data) VALUES (7,'Existing event','{}')",
        ):
            await conn.execute(text(sql))
    app = FastAPI()
    install_admin(app, engine, settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://console.test") as client:
        client.cookies.set(settings.auth_cookie_name, "admin")
        response = await client.post("/console/event-periods/create", headers={"Origin": "http://console.test"},
                                     data={"event_id": "7", "server": "europe", "game_version": "3.8",
                                           "starts_at": "", "ends_at": "1900-01-02T03:04:00"})
        assert response.status_code == 303, response.text[-1500:]
        response = await client.post("/console/event-periods/create", headers={"Origin": "http://console.test"},
                                     data={"event_id": "7", "server": "europe", "game_version": "3.8",
                                           "starts_at": "2000-01-01T00:00:00", "ends_at": ""})
        assert response.status_code == 303, response.text[-1500:]
        listing = await client.get("/console/api/event-periods")
        assert listing.status_code == 200 and "Existing event" in listing.text
        invalid = {"game_version": "3.8", "starts_at": "1900-01-03T00:00:00", "ends_at": "1900-01-02T00:00:00"}
        response = await client.post("/console/event-periods/edit/1", headers={"Origin": "http://console.test"}, data=invalid)
        assert response.status_code == 422
        valid = {**invalid, "starts_at": ""}
        response = await client.post("/console/event-periods/edit/1", headers={"Origin": "http://console.test"}, data=valid)
        assert response.status_code == 303, response.text[-1500:]
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT event_id,source_occurrence_id,starts_at,ends_at,source_data FROM core.game_event_occurrence ORDER BY id"))).all()
        assert len(rows) == 2 and rows[0].source_occurrence_id != rows[1].source_occurrence_id
        row = rows[0]
        assert row.event_id == 7 and row.source_occurrence_id.startswith("manual:") and row.starts_at is None
        assert "1900-01-02 00:00:00" in row.ends_at and '"manual"' in row.source_data
        assert await conn.scalar(text("SELECT count(*) FROM ops.agent_tool_audit WHERE tool='console.event_period.create'")) == 2
        assert await conn.scalar(text("SELECT count(*) FROM ops.agent_tool_audit WHERE tool='console.event_period.edit'")) == 1
    await engine.dispose()
