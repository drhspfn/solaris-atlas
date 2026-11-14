"""Tests for the migrated /console operations views (Data Operations, Story Agent, Cutscene Analysis)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.admin import cutscene_analysis, operations, story_agent
from wuwa_story.admin.auth import InternalAuth
from wuwa_story.admin.composition import install_admin
from wuwa_story.config.settings import Settings


@pytest.fixture
async def console_client(monkeypatch):
    engine = create_async_engine(
        "postgresql+asyncpg://user:pass@localhost:5432/unused")
    settings = Settings(_env_file=None, app_env="test",
                        auth_cookie_secure=False)

    monkeypatch.setattr(
        operations,
        "load_overview",
        AsyncMock(
            return_value={
                "releases": [
                    {
                        "id": 1,
                        "game_version": "2.4.0",
                        "sequence": 10,
                        "is_current": True,
                        "is_imported": True,
                        "quests": 42,
                        "cutscenes": 7,
                        "characters": 12,
                        "snapshots": 1,
                    }
                ],
                "imports": [],
                "maps": [],
                "client_assets": {"installed": [], "active_client": None, "active_download": None},
            }
        ),
    )
    monkeypatch.setattr(
        operations,
        "queue_status",
        AsyncMock(
            return_value={
                "available": True,
                "error": None,
                "queues": [
                    {
                        "name": "wuwa.story.import",
                        "ready": 2,
                        "active": 1,
                        "consumers": 1,
                        "state": "running",
                        "workers": [],
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        story_agent,
        "load_usage",
        AsyncMock(
            return_value={
                "daily_budget_usd": "10.00",
                "daily_token_limit": 1000000,
                "timezone": "UTC",
                "today": "2026-11-14",
                "days": [
                    {
                        "day": "2026-11-14",
                        "spent_usd": "1.25",
                        "reserved_usd": "0.10",
                        "spent_tokens": 5000,
                        "reserved_tokens": 200,
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        story_agent,
        "load_jobs",
        AsyncMock(
            return_value={
                "jobs": [
                    {
                        "id": 77,
                        "status": "completed",
                        "request": {"quest_id": 1001},
                        "mode": "analysis",
                        "error": None,
                        "tokens_input": 1200,
                        "tokens_output": 300,
                        "cost_usd": 0.0123,
                        "step": 3,
                        "max_steps": 12,
                        "model": "gemini-test",
                    }
                ],
                "next_before": None,
            }
        ),
    )
    monkeypatch.setattr(
        story_agent,
        "load_revisits",
        AsyncMock(return_value={"revisits": [], "next_before": None}),
    )
    monkeypatch.setattr(
        cutscene_analysis,
        "load_assets",
        AsyncMock(
            return_value={
                "enabled": True,
                "assets": [{"id": 501, "reference": "cs/intro.mp4", "game_version": "2.4.0"}],
                "next_before": None,
            }
        ),
    )
    monkeypatch.setattr(
        cutscene_analysis,
        "load_visual_jobs",
        AsyncMock(
            return_value={
                "jobs": [
                    {
                        "id": 88,
                        "asset_node_id": 501,
                        "reference": "cs/intro.mp4",
                        "game_version": "2.4.0",
                        "status": "running",
                        "title_only": False,
                        "title_generated": None,
                        "step": 1,
                        "frames_done": 10,
                        "frames_remaining": 5,
                        "error": None,
                        "tokens_input": 400,
                        "tokens_output": 100,
                        "cost_usd": 0.005,
                        "output_tokens": 2048,
                    }
                ],
                "next_before": None,
            }
        ),
    )

    app = FastAPI()
    install_admin(app, engine, settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://console.test"
    ) as client:
        yield client, monkeypatch
    await engine.dispose()


@pytest.mark.parametrize(
    "path",
    [
        "/console/data-operations",
        "/console/story-agent",
        "/console/cutscene-analysis",
    ],
)
async def test_operations_views_require_authentication(console_client, path):
    client, _ = console_client
    res = await client.get(path)
    assert res.status_code == 303


async def test_data_operations_view_renders_ssr_data_and_controls(console_client):
    client, monkeypatch = console_client
    monkeypatch.setattr(InternalAuth, "is_authenticated",
                        AsyncMock(return_value=True))
    res = await client.get("/console/data-operations")
    assert res.status_code == 200
    body = res.text
    assert "sa-data-ops" in body
    assert "2.4.0" in body
    assert "wuwa.story.import" in body
    assert "data-operations.js" in body
    assert "Auto-refresh: Off" in body


async def test_story_agent_view_renders_ssr_data_and_controls(console_client):
    client, monkeypatch = console_client
    monkeypatch.setattr(InternalAuth, "is_authenticated",
                        AsyncMock(return_value=True))
    res = await client.get("/console/story-agent")
    assert res.status_code == 200
    body = res.text
    assert "sa-story-agent" in body
    assert "$1.25" in body
    assert "#77" in body
    assert "story-agent.js" in body
    assert "Auto: Off" in body


async def test_cutscene_analysis_view_renders_ssr_data_and_controls(console_client):
    client, monkeypatch = console_client
    monkeypatch.setattr(InternalAuth, "is_authenticated",
                        AsyncMock(return_value=True))
    res = await client.get("/console/cutscene-analysis")
    assert res.status_code == 200
    body = res.text
    assert "sa-cutscene-analysis" in body
    assert "cs/intro.mp4" in body
    assert "#88" in body
    assert "cutscene-analysis.js" in body
    assert "Auto: Off" in body
