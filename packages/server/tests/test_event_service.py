"""Event use cases run against the repository port without SQL or storage clients."""

from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from wuwa_story.api.routes import game_events
from wuwa_story.events.domain import (
    Event,
    EventAnnouncement,
    EventMedia,
    EventNoticeSnapshot,
    EventOccurrence,
    EventRun,
    EventRunNotFound,
    _client_data,
    _presentation,
)
from wuwa_story.events.repository import EventRepository
from wuwa_story.events.service import EventService


@pytest.fixture
def event():
    return Event(
        id=7,
        slug="event",
        source_id="client:7",
        title="Event",
        description="Description",
        event_kind="banner",
        banner_path="UI/banner.png",
        rewards=[],
        game_path="/Game/Event",
        source_url="https://official/event",
        source_data={
            "client": {
                "download_id": "current",
                "asset_version": "3.7.0",
                "locales": {"en": {"title": "Current title"}},
                "artwork": [{"path": "UI/banner.png", "role": "content"}],
            }
        },
    )


@pytest.fixture
def repository():
    repository = AsyncMock(spec=EventRepository)
    for method in ("runs", "announcements", "notice_snapshots", "reward_nodes", "media"):
        getattr(repository, method).return_value = []
    return repository


@pytest.fixture
def service(repository):
    return EventService(repository, lambda key: f"https://cdn/{key}", lambda url: url)


@pytest.mark.asyncio
async def test_undated_calendar_and_detail_keep_public_contract(event, repository, service):
    repository.runs.return_value = [EventRun(event, None)]
    repository.media.return_value = [
        EventMedia("UI/banner.png", "current.png", {"download_id": "current"})
    ]
    result = await service.browse("asia", locale="fr", summary=True)
    repository.runs.assert_awaited_once_with(
        server="asia",
        kind=None,
        version=None,
        locale="fr",
        summary=True,
        event_id=None,
        occurrence_id=None,
    )
    payload = result["events"][0]
    assert result == {
        "server": "asia",
        "source": "wuwa-game-client",
        "source_url": "https://wutheringwaves.kurogames.com/",
        "catalog_status": "ready",
        "events": [
            {
                "id": 7,
                "slug": "event",
                "href": "/events?event=event",
                "source_id": "client:7",
                "occurrence_id": None,
                "title": "Current title",
                "description": "Description",
                "kind": "banner",
                "convene_type": None,
                "game_version": None,
                "starts_at": None,
                "ends_at": None,
                "season": None,
                "rewards": [],
                "featured_names": [],
                "up_names": [],
                "banner_path": "UI/banner.png",
                "banner_url": "https://cdn/current.png",
                "artwork": [
                    {"path": "UI/banner.png", "role": "content", "url": "https://cdn/current.png"}
                ],
                "preview_url": "https://cdn/current.png",
                "preview_provenance": "game_client",
                "catalog_version": "3.7.0",
                "schedule_status": "server_controlled",
                "hidden_in_client": False,
                "game_path": "/Game/Event",
                "source_url": "https://official/event",
                "official_url": None,
                "notices": [],
                "detail_url": "/events/7/detail?server=asia&locale=fr",
            }
        ],
    }
    detail = await service.detail(7, "asia", locale="fr")
    assert detail == {key: value for key, value in payload.items() if key != "detail_url"}
    repository.runs.assert_awaited_with(
        server="asia",
        kind=None,
        version=None,
        locale="fr",
        summary=False,
        event_id=7,
        occurrence_id=None,
    )


@pytest.mark.asyncio
async def test_historical_capture_and_notice_identity_remain_scoped(event, repository, service):
    occurrence = EventOccurrence(
        id=42,
        game_version="3.6",
        starts_at=None,
        ends_at=None,
        season=None,
        banner_path=None,
        source_data={
            "client": {"download_id": "old", "locales": {"en": {"title": "Old title"}}},
            "notice_id": 11,
            "notice_revision": "shared",
        },
    )
    repository.runs.return_value = [EventRun(event, occurrence)]
    repository.media.return_value = [
        EventMedia("UI/banner.png", "current.png", {"download_id": "current"}),
        EventMedia("capture/UI/banner.png", "old.png", {"download_id": "old"}),
    ]
    repository.announcements.return_value = [
        EventAnnouncement(
            11,
            "notice",
            "Notice",
            [7],
            None,
            {"banner_url": "https://official/current.png"},
        )
    ]
    repository.notice_snapshots.return_value = [
        EventNoticeSnapshot(11, "shared", {"banner_url": "https://official/old.png"}),
        EventNoticeSnapshot(99, "shared", {"banner_url": "https://official/other.png"}),
    ]
    payload = await service.detail(7, "europe", 42, "fr")
    assert payload["title"] == "Old title"
    assert payload["banner_url"] == "https://cdn/old.png"
    assert payload["preview_url"] == "https://official/old.png"
    assert payload["preview_provenance"] == "notice_snapshot"
    repository.notice_snapshots.assert_awaited_once_with({"shared"})
    repository.media.assert_awaited_once_with(
        {"UI/banner.png"}, captures={("old", "UI/banner.png")}
    )
    assert event.source_data["client"]["download_id"] == "current"


def test_matching_capture_uses_enriched_current_metadata_and_notice_locale_overlay(event):
    occurrence = EventOccurrence(
        42,
        "3.7",
        None,
        None,
        None,
        None,
        {
            "client": {"download_id": "current", "locales": {"en": {"title": "Stale"}}},
            "notice_presentation": {"fr": {"title": "Pinned title"}},
        },
    )
    assert _client_data(event, occurrence) == event.source_data["client"]
    assert _presentation(event, occurrence, "fr") == {"title": "Pinned title"}
    assert _presentation(event, occurrence, "ja") == {"title": "Current title"}
    older = replace(occurrence, source_data={"client": {"download_id": "old"}})
    assert _client_data(event, older) == {"download_id": "old"}


@pytest.mark.asyncio
async def test_empty_calendar_and_missing_detail_have_distinct_outcomes(repository, service):
    result = await service.browse("europe")
    assert result["catalog_status"] == "not_imported" and result["events"] == []
    repository.media.assert_not_awaited()
    with pytest.raises(EventRunNotFound, match="Event run not found"):
        await service.detail(7, "europe", 999)


@pytest.mark.asyncio
async def test_http_routes_use_overridden_service_and_map_domain_errors(monkeypatch):
    monkeypatch.setattr(
        game_events.read_projections, "should_route_to_projection", lambda *a, **kw: False
    )
    service = AsyncMock(spec=EventService)
    service.browse.return_value = {"events": []}
    service.detail.side_effect = EventRunNotFound()
    app = FastAPI()
    app.include_router(game_events.router)
    app.dependency_overrides[game_events.get_event_service] = lambda: service
    app.dependency_overrides[game_events.get_session] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/events?server=asia&kind=banner&version=3.7&locale=fr&summary=true"
        )
        assert response.status_code == 200 and response.json() == {"events": []}
        missing = await client.get("/events/7/detail?server=america&occurrence_id=42&locale=fr")
    service.browse.assert_awaited_once_with("asia", "banner", "3.7", "fr", summary=True)
    service.detail.assert_awaited_once_with(7, "america", 42, "fr")
    assert missing.status_code == 404 and missing.json() == {"detail": "Event run not found"}
