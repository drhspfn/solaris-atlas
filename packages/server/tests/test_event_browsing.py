"""Regression checks for lightweight calendars and complete, run-scoped galleries."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes import game_events
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.repositories.event_browsing import read_event_media, read_event_runs
from wuwa_story.events import service as event_service


def event():
    return SimpleNamespace(
        id=7, slug="7-event", source_id="client:7", title="Event", description="Description",
        event_kind="banner", banner_path="/Game/Banner", rewards=[], game_path="/Game/Event",
        source_url="https://example.test", source_data={"client": {
            "download_id": "current", "locales": {"en": {"title": "Localized event"}},
            "artwork": [{"path": "/Game/Banner", "role": "featured_portrait"},
                        {"path": "/Game/Background", "role": "background"}],
            "prefab_artwork": [{"path": f"/Game/Texture{i}", "role": "prefab_texture"}
                               for i in range(1000)],
        }},
    )


@pytest.mark.asyncio
async def test_default_list_and_detail_keep_full_gallery_while_summary_is_explicit(monkeypatch):
    record = event()
    read = AsyncMock(return_value=[(record, None)])
    monkeypatch.setattr(event_service.repository, "read_event_runs", read)
    monkeypatch.setattr(event_service, "get_settings", lambda: SimpleNamespace(s3_bucket="test"))
    monkeypatch.setattr(event_service, "S3Storage", lambda _: SimpleNamespace(public_url=lambda k: k))
    session = AsyncMock()
    session.scalars.return_value = []
    session.execute.return_value = [
        ("/Game/Banner", "https://cdn.test/banner", {"download_id": "current"}),
        ("/Game/Background", "https://cdn.test/background", {"download_id": "current"}),
    ]
    app = FastAPI()
    app.include_router(game_events.router)
    app.dependency_overrides[game_events.get_session] = lambda: session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        full = (await client.get("/events")).json()["events"][0]
        assert read.call_args.kwargs["summary"] is False
        assert len(full["artwork"]) == 1002
        assert "detail_url" not in full
        assert full["preview_url"] == "https://cdn.test/banner"
        assert full["title"] == "Localized event"

        record.source_data["client"]["prefab_artwork"] = []
        summary = (await client.get("/events?summary=true&server=asia&locale=fr")).json()["events"][0]
        assert read.call_args.kwargs["summary"] is True
        assert read.call_args.kwargs["server"] == "asia"
        assert summary["detail_url"] == "/events/7/detail?server=asia&locale=fr"
        assert summary["preview_url"] == full["preview_url"]
        assert summary["title"] == full["title"]

        record.source_data = event().source_data
        detail = (await client.get("/events/7/detail?server=asia&occurrence_id=42&locale=fr")).json()
        assert len(detail["artwork"]) == 1002
        assert read.call_args.kwargs == {
            "server": "asia", "kind": None, "version": None, "locale": "fr", "summary": False,
            "event_id": 7, "occurrence_id": 42,
        }
        read.return_value = []
        assert (await client.get("/events/7/detail?occurrence_id=999")).status_code == 404
        assert (await client.get("/events?summary=true&server=invalid")).status_code == 422


@pytest.mark.asyncio
async def test_summary_projects_json_in_sql_and_preserves_undated_rows_and_filters():
    record = event()
    flat = tuple(getattr(record, column.key, None) for column in GameEvent.__table__.columns)
    flat += (None,) * len(GameEventOccurrence.__table__.columns)
    session = AsyncMock()
    session.execute.return_value = Mock(all=Mock(return_value=[flat]))
    rows = await read_event_runs(
        session, server="europe", kind="banner", version="3.7", locale="ru", summary=True,
    )
    assert rows[0][0].id == record.id
    assert rows[0][1] is None
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "CAST(" in sql and "AS JSONPATH" in sql
    assert "jsonb_path_query_array" in sql
    assert "LEFT OUTER JOIN" in sql
    assert "starts_at DESC NULLS LAST" in sql
    assert "game_version IS NULL" in sql
    assert {"europe", "banner", "3.7", "ru"} <= set(
        value for value in compiled.params.values() if isinstance(value, str)
    )
    # Neither raw JSONB column may be selected alongside its bounded projection.
    assert "source_data" not in [column.key for column in statement.selected_columns]


@pytest.mark.asyncio
@pytest.mark.parametrize("occurrence_id", [None, 42])
async def test_detail_query_constrains_event_server_and_exact_occurrence(occurrence_id):
    session = AsyncMock()
    session.execute.return_value = Mock(all=Mock(return_value=[]))
    await read_event_runs(session, server="america", event_id=7, occurrence_id=occurrence_id)
    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "america" in compiled.params.values()
    assert 7 in compiled.params.values()
    if occurrence_id is None:
        assert "game_event_occurrence.id IS NULL" in str(compiled)
    else:
        assert 42 in compiled.params.values()
        assert "game_event_occurrence.id =" in str(compiled)


@pytest.mark.asyncio
async def test_media_lookup_keeps_suffix_fallback_capture_metadata_and_published_image_filters():
    session = AsyncMock()
    await read_event_media(session, {"/Game/Banner", "UI/a_b.png"}, "test")
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "LIKE" in sql
    assert "metadata" in sql
    assert "test" in compiled.params.values()
    assert "file_location.is_primary IS true" in sql
    assert "file_location.available IS true" in sql
    assert "image/png" in str(compiled.params)


@pytest.mark.asyncio
async def test_service_joins_pinned_notice_presentation_and_reward_addresses(monkeypatch):
    record = event()
    occurrence = SimpleNamespace(
        id=42, game_version="3.7", starts_at=datetime(2026, 10, 1, tzinfo=UTC),
        ends_at=datetime(2026, 10, 22, tzinfo=UTC), season=None, banner_path=None,
        source_data={"notice_id": 11, "notice_revision": "archived",
                     "client": {"download_id": "current"},
                     "notice_presentation": {"ru": {
                         "title": "Pinned title", "rewards": [{"id": 123, "name": "Astrite"}],
                     }}},
    )
    monkeypatch.setattr(event_service.repository, "read_event_runs",
                        AsyncMock(return_value=[(record, occurrence)]))
    monkeypatch.setattr(event_service, "get_settings", lambda: SimpleNamespace(s3_bucket="test"))
    monkeypatch.setattr(event_service, "S3Storage", lambda _: SimpleNamespace(public_url=lambda k: k))
    notice = SimpleNamespace(
        id=11, slug="11-notice", title="Official notice", related_event_ids=[record.id],
        published_at=None, content={"banner_url": "https://cdn.test/current"},
    )
    snapshot = SimpleNamespace(
        notice_id=11, revision="archived", content={"banner_url": "https://cdn.test/archived"},
    )
    reward = SimpleNamespace(id=123, canonical_key="item:123", slug="123-astrite")
    session = AsyncMock()
    session.scalars.side_effect = [[notice], [snapshot], [reward]]
    session.execute.return_value = []
    result = await event_service.browse_events("europe", None, None, "ru", session, summary=True)
    payload = result["events"][0]
    assert payload["title"] == "Pinned title"
    assert payload["preview_url"] == "https://cdn.test/archived"
    assert payload["preview_provenance"] == "notice_snapshot"
    assert payload["rewards"] == [{"id": 123, "name": "Astrite", "slug": "123-astrite",
                                    "href": "/items/123-astrite"}]
    assert payload["notices"] == [{"id": 11, "title": "Official notice", "url": "/notices/11-notice"}]
    statements = [call.args[0].compile(dialect=postgresql.dialect())
                  for call in session.scalars.call_args_list]
    assert "en" in statements[0].params.values()
    assert ["archived"] in statements[1].params.values()
    assert ["item:123"] in statements[2].params.values()


@pytest.mark.asyncio
async def test_optional_repository_reads_skip_empty_inputs():
    session = AsyncMock()
    assert await event_service.repository.read_event_notice_snapshots(session, set()) == []
    assert await event_service.repository.read_event_reward_nodes(session, set()) == []
    session.scalars.assert_not_awaited()


def test_route_helper_exports_remain_compatible():
    for name in ("_artwork", "_choose_preview", "_client_data", "_client_preview",
                 "_notice_overlaps", "_official_announcement", "_presentation"):
        assert getattr(game_events, name) is getattr(event_service, name)
    assert game_events._game_events_response is event_service.browse_events
