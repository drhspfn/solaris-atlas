from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.admin import views
from wuwa_story.admin.auth import InternalAuth
from wuwa_story.admin.composition import install_admin
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.config.settings import Settings
from wuwa_story.db.models.agents import AgentDailyUsage


def test_budget_page_renders_live_settings(monkeypatch):
    settings = AgentSettings(_env_file=None, daily_budget_usd=Decimal("12.50"),
                             daily_token_limit=5000, budget_timezone="Pacific/Honolulu")
    monkeypatch.setattr(views, "get_agent_settings", lambda: settings)
    model = views.BudgetUsage(AgentDailyUsage, identity="usage")
    templates = Path(views.__file__).parent / "templates"
    environment = Environment(loader=ChoiceLoader([
        FileSystemLoader(templates), DictLoader(
            {"list.html": "{% block head_css %}{% endblock %}{% block content %}ledger{% endblock %}"})
    ]), autoescape=select_autoescape())
    html = environment.get_template("budget_usage.html").render(model=model)
    assert "$12.50" in html and "5,000" in html
    assert "Pacific/Honolulu" in html
    assert str(datetime.now(UTC).astimezone(
        ZoneInfo(settings.budget_timezone)).date()) in html
    assert "Reservations remain held" in html and "ledger" in html
    assert model.fields_default_sort == [("day", True)]


async def test_budget_page_uses_real_admin_templates(monkeypatch):
    async def authenticated(self, request):
        return True

    monkeypatch.setattr(InternalAuth, "is_authenticated", authenticated)
    monkeypatch.setattr(InternalAuth, "get_admin_user",
                        lambda self, request: None)
    engine = create_async_engine("sqlite+aiosqlite://")
    app = FastAPI()
    install_admin(app, engine, Settings(_env_file=None, app_env="test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/console/usage/list")
        users = await client.get("/console/users/create")
        stylesheet = await client.get("/console/statics/solaris-admin.css")
    await engine.dispose()
    assert response.status_code == 200
    assert "Daily budget overview" in response.text
    assert 'id="dt"' in response.text
    assert "data-bs-theme" in response.text and "solaris-admin.css" in response.text
    assert users.status_code == 200 and "solaris-admin.css" in users.text
    assert stylesheet.status_code == 200 and "--tblr-body-bg: #090e0f" in stylesheet.text
