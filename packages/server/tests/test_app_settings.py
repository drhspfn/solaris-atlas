"""Tests for generic ops.app_setting registry, agent scope split, admin settings UI, and usage bugfixes."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from starlette_admin import RequestAction

from wuwa_story.admin.auth import InternalAuth
from wuwa_story.admin.composition import install_admin
from wuwa_story.admin.views import BudgetUsage, _parse_usage_day
from wuwa_story.agents.budget import BudgetExceeded, ContextLimitExceeded
from wuwa_story.agents.settings import (
    AgentSettings,
    agent_db_override_fields,
    load_effective_agent_settings,
)
from wuwa_story.api.routes import admin_settings
from wuwa_story.api.routes.cutscene_analysis import public_job
from wuwa_story.auth.constants import UserRole
from wuwa_story.auth.dependencies import get_auth_context, require_admin, require_csrf
from wuwa_story.config.app_settings import (
    SettingSpec,
    get_setting,
    parse_setting_value,
    serialize_setting,
    sync_setting_definitions,
)
from wuwa_story.config.settings import Settings
from wuwa_story.db.models.agents import AgentDailyUsage
from wuwa_story.db.models.auth import User
from wuwa_story.db.models.ops import AppSetting
from wuwa_story.db.session import get_session


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


def test_parse_setting_value_validates_all_types():
    int_spec = SettingSpec(
        key="test.int", category="Test", name="Int", value_type="integer", validation={"min": 10, "max": 20}
    )
    assert parse_setting_value(int_spec, "15") == (15, "15")
    with pytest.raises(ValueError, match=">="):
        parse_setting_value(int_spec, 5)
    with pytest.raises(ValueError, match="integer"):
        parse_setting_value(int_spec, "15.5")

    enum_spec = SettingSpec(
        key="test.enum", category="Test", name="Enum", value_type="enum", choices=("a", "b")
    )
    assert parse_setting_value(enum_spec, "b") == ("b", "b")
    with pytest.raises(ValueError, match="Expected one of"):
        parse_setting_value(enum_spec, "c")

    list_spec = SettingSpec(
        key="test.list",
        category="Test",
        name="List",
        value_type="list",
        multi=True,
        choices=("core", "story", "graph"),
        validation={"min_items": 1},
    )
    assert parse_setting_value(list_spec, ["core", "story", "core"]) == (
        ["core", "story"],
        '["core", "story"]',
    )
    assert parse_setting_value(list_spec, "core, graph") == (
        ["core", "graph"],
        '["core", "graph"]',
    )
    with pytest.raises(ValueError, match="Unsupported choice"):
        parse_setting_value(list_spec, ["invalid"])
    with pytest.raises(ValueError, match="at least 1"):
        parse_setting_value(list_spec, [])

    secret_spec = SettingSpec(
        key="test.secret",
        category="Test",
        name="Secret",
        value_type="secret",
        is_secret=True,
        validation={"min_length": 4},
    )
    assert parse_setting_value(
        secret_spec, "sk-12345") == ("sk-12345", "sk-12345")
    with pytest.raises(ValueError, match="at least 4"):
        parse_setting_value(secret_spec, "sk")


def test_serialize_setting_masks_secrets():
    spec = SettingSpec(
        key="agent.provider.api_key",
        category="Agent · Credentials",
        name="API Key",
        value_type="secret",
        is_secret=True,
        default="env-secret-value",
    )
    row = AppSetting(
        key=spec.key,
        category=spec.category,
        name=spec.name,
        value_type="secret",
        value="db-secret-value",
        validation={},
        multi=False,
        is_secret=True,
    )
    serialized = serialize_setting(spec, row)
    assert serialized["is_configured"] is True
    assert serialized["effective_value"] == "••••••••"
    assert serialized["default_value"] == "••••••••"
    assert "db-secret-value" not in str(serialized)
    assert "env-secret-value" not in str(serialized)


@pytest.mark.asyncio
async def test_app_settings_sqlite_sync_and_split_agent_scopes():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        execution_options={"schema_translate_map": {
            "ops": None, "auth": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(AppSetting.__table__.create)
        await conn.run_sync(AgentDailyUsage.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    base = AgentSettings(
        _env_file=None,
        model="base-model",
        daily_budget_usd=Decimal("1.00"),
        max_input_tokens=250000,
    )
    async with factory() as db:
        await sync_setting_definitions(db)
        rows = await sync_setting_definitions(db)
        rows["agent.budget.daily_budget_usd"].value = "7.50"
        rows["agent.story.model"].value = "story-specific-model"
        rows["agent.cutscene.model"].value = "cutscene-vision-model"
        rows["agent.cutscene.max_input_tokens"].value = "400000"
        rows["agent.story.max_steps"].value = "24"
        await db.commit()

        story_cfg = await load_effective_agent_settings(db, base, scope="story")
        cutscene_cfg = await load_effective_agent_settings(db, base, scope="cutscene")
        assert story_cfg.model == "story-specific-model"
        assert story_cfg.max_steps == 24
        assert story_cfg.max_input_tokens == 250000
        assert story_cfg.daily_budget_usd == Decimal("7.50")

        assert cutscene_cfg.model == "cutscene-vision-model"
        assert cutscene_cfg.max_input_tokens == 400000
        assert cutscene_cfg.daily_budget_usd == Decimal("7.50")
        assert await get_setting(db, "agent.budget.daily_budget_usd") == Decimal("7.50")

        overrides = await agent_db_override_fields(db, scope="cutscene")
        assert overrides["model"] == "cutscene-vision-model"
        assert overrides["max_input_tokens"] == 400000

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_settings_endpoints_and_console_view(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        execution_options={"schema_translate_map": {
            "ops": None, "auth": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(AppSetting.__table__.create)
        await conn.run_sync(AgentDailyUsage.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        admin_user = User(
            id=1,
            email="admin@example.com",
            email_normalized="admin@example.com",
            nickname="admin",
            nickname_normalized="admin",
            password_hash="x",
            role=UserRole.ADMIN,
            status=1,
        )
        db.add(admin_user)
        db.add(
            AgentDailyUsage(
                day=date(2026, 11, 20),
                spent_usd=Decimal("0.20413380"),
                reserved_usd=Decimal("0E-8"),
                spent_tokens=4224259,
                reserved_tokens=0,
            )
        )
        await db.commit()
        await db.refresh(admin_user)

    app = FastAPI()
    app.include_router(admin_settings.router)

    async def get_db():
        async with factory() as s:
            yield s

    async def auth_ctx():
        return SimpleNamespace(user=admin_user)

    app.dependency_overrides[get_session] = get_db
    app.dependency_overrides[get_auth_context] = auth_ctx
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.dependency_overrides[require_csrf] = lambda: None

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/admin/settings")
        assert listed.status_code == 200
        body = listed.json()
        assert "Agent · Budget & Limits" in body["categories"]
        assert "Agent · Story Analysis" in body["categories"]
        assert "Agent · Cutscene Vision" in body["categories"]

        updated = await client.put(
            "/admin/settings/agent.budget.daily_budget_usd",
            json={"value": "5.25", "reset": False},
        )
        assert updated.status_code == 200
        assert updated.json()["setting"]["effective_value"] == "5.25"
        assert updated.json()["setting"]["source"] == "database"

        invalid = await client.put(
            "/admin/settings/agent.cutscene.provider",
            json={"value": "unknown-provider", "reset": False},
        )
        assert invalid.status_code == 422

        reset = await client.put(
            "/admin/settings/agent.budget.daily_budget_usd",
            json={"reset": True},
        )
        assert reset.status_code == 200
        assert reset.json()["setting"]["source"] == "default"

    # Verify BudgetUsage detail view accepts formatted "November 20, 2026" and ISO "2026-11-20"
    assert _parse_usage_day("November 20, 2026") == date(2026, 11, 20)
    assert _parse_usage_day("2026-11-20") == date(2026, 11, 20)
    assert _parse_usage_day("not-a-date") is None

    monkeypatch.setattr(InternalAuth, "is_authenticated",
                        AsyncMock(return_value=True))
    monkeypatch.setattr(InternalAuth, "get_admin_user",
                        lambda self, req: admin_user)
    console_app = FastAPI()
    install_admin(console_app, engine, Settings(
        _env_file=None, app_env="test"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=console_app), base_url="http://test"
    ) as console_client:
        settings_page = await console_client.get("/console/settings")
        assert settings_page.status_code == 200
        assert "Settings" in settings_page.text
        assert "agent.cutscene.model" in settings_page.text
        assert "agent.story.model" in settings_page.text
        assert "agent.budget.daily_budget_usd" in settings_page.text

        usage_detail = await console_client.get("/console/usage/detail/November%2020,%202026")
        assert usage_detail.status_code == 200
        assert "0.20413380" in usage_detail.text
        assert "0.00000000" in usage_detail.text

        bad_detail = await console_client.get("/console/usage/detail/not-a-date")
        assert bad_detail.status_code == 404

    # Test BudgetUsage direct lookup + Decimal formatting without 0E-8
    view = BudgetUsage(AgentDailyUsage, identity="usage")
    async with factory() as s:
        fake_req = SimpleNamespace(state=SimpleNamespace(
            session=s, action=RequestAction.DETAIL))
        found_pretty = await view.find_by_pk(fake_req, "November 20, 2026")
        found_iso = await view.find_by_pk(fake_req, "2026-11-20")
        not_found = await view.find_by_pk(fake_req, "invalid-date")
        assert found_pretty is not None and found_pretty.day == date(
            2026, 11, 20)
        assert found_iso is not None and found_iso.day == date(2026, 11, 20)
        assert not_found is None
        formatted_zero = await view.serialize_field_value(
            Decimal("0E-8"), SimpleNamespace(), RequestAction.LIST, fake_req
        )
        assert formatted_zero == "0.00000000"

    await engine.dispose()


def test_public_job_and_context_limit_subclass():
    assert issubclass(ContextLimitExceeded, BudgetExceeded)
    run = SimpleNamespace(
        id=40978,
        target_node_id=7458657,
        status="paused_context",
        metadata_json={"asset_version": "3.8.0",
                       "settings": {"vision_output_tokens": 8192}},
        raw_output={
            "mode": "video_agent",
            "step": 10,
            "atlases": 13,
            "review_draft": {"events": [{"a": 1}, {"a": 2}]},
        },
        error="Input context exceeds the configured token bound: estimated=260000, limit=250000",
        tokens_input=775285,
        tokens_output=5665,
        cost=0.039103,
    )
    payload = public_job(run)
    assert payload["mode"] == "video_agent"
    assert payload["atlases"] == 13
    assert payload["events_done"] == 2
    assert payload["phase"] == "review"
