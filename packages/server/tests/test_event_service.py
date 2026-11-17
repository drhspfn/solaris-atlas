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
                "start_estimate": None,
                "ends_at": None,
                "season": None,
                "rewards": [],
                "featured_names": [],
                "up_names": [],
                "banner_path": "UI/banner.png",
                "banner_url": "https://cdn/current.png",
                "artwork": [
                    {"path": "UI/banner.png", "role": "content",
                        "url": "https://cdn/current.png"}
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
    assert detail == {key: value for key,
                      value in payload.items() if key != "detail_url"}
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
@pytest.mark.parametrize("summary", [True, False])
async def test_activity_background_precedes_tab_icon(event, repository, service, summary):
    background = "/Game/Aki/UI/UIResources/Common/Image/BgCgBig/T_CircumfluenceStarBg"
    tab = "/Game/Aki/UI/UIResources/UiActivity/Image/Activity33/CatapultStory/T_ActivityTab_CatapultStory"
    client = {"download_id": "current", "artwork": [
        {"path": background, "role": "background", "table": "Activity"},
        {"path": tab, "role": "tab", "table": "Activity"},
    ]}
    record = replace(event, event_kind="permanent", banner_path=background,
                     source_data={"client": client})
    repository.runs.return_value = [EventRun(record, None)]
    repository.media.return_value = [
        EventMedia(background, "background.png", {"download_id": "current"}),
        EventMedia(tab, "tab.png", {"download_id": "current"}),
    ]
    result = await service.browse("asia", summary=summary)
    assert result["events"][0]["preview_url"] == "https://cdn/background.png"


def test_preview_preserves_content_and_falls_back_for_missing_or_placeholder_background():
    from wuwa_story.events.domain import _client_preview

    background = {"path": "/Game/Background",
                  "role": "background", "table": "Activity"}
    tab = {"path": "/Game/Tab", "role": "tab", "table": "Activity"}
    content = {"path": "/Game/Content",
               "role": "content", "table": "GachaViewInfo"}

    def resolve(path):
        return path if path != "/Game/Background" else None
    assert _client_preview([background, tab], resolve) == "/Game/Tab"
    assert _client_preview([background, tab, content],
                           lambda path: path) == "/Game/Content"
    black = {**background, "path": "/Game/T_CommonBlack_UI"}
    assert _client_preview([black, tab], lambda path: path) == "/Game/Tab"
    gacha = {**background, "table": "GachaViewInfo"}
    assert _client_preview([gacha, tab], lambda path: path) == "/Game/Tab"


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", [True, False])
async def test_verified_prefab_preview_excludes_shared_activity_background(event, repository, service, summary):
    from wuwa_story.events.domain import PREFAB_PREVIEWS, SHARED_ACTIVITY_BACKGROUNDS

    source_id = "activity:108700001"
    prefab, image = PREFAB_PREVIEWS[source_id]
    shared = "/Game/Aki/UI/UIResources/Common/Image/BgCgBig/T_ActivityProcessMemoryBg"
    assert shared in SHARED_ACTIVITY_BACKGROUNDS
    client = {"download_id": "current", "artwork": [
        {"path": shared, "role": "background", "table": "Activity"},
    ], "prefab_artwork": [{"path": image, "role": "prefab_texture", "prefab": prefab}]}
    record = replace(event, source_id=source_id, event_kind="permanent", banner_path=shared,
                     source_data={"client": client})
    repository.runs.return_value = [EventRun(record, None)]
    repository.media.return_value = [
        EventMedia(shared, "shared.png", {"download_id": "current"}),
        EventMedia(image, "soar.png", {"download_id": "current"}),
        EventMedia(image, "wrong-version.png", {"download_id": "old"}),
    ]
    payload = (await service.browse("asia", summary=summary))["events"][0]
    assert payload["preview_url"] == "https://cdn/soar.png"
    assert payload["banner_url"] is None

    # Shared art and a prefab belonging to another activity cannot become a preview.
    repository.runs.return_value = [
        EventRun(replace(record, source_id="activity:102000009"), None)]
    payload = (await service.browse("asia", summary=summary))["events"][0]
    assert payload["preview_url"] is None and payload["banner_url"] is None


def test_prefab_preview_requires_exact_prefab_and_available_capture():
    from wuwa_story.events.domain import (
        PREFAB_PREVIEWS,
        SHARED_ACTIVITY_BACKGROUNDS,
        _client_preview,
    )

    source = "activity:108700001"
    prefab, path = PREFAB_PREVIEWS[source]
    asset = {"role": "prefab_texture", "path": path, "prefab": prefab}
    assert _client_preview([asset], lambda _: None, source) is None
    assert _client_preview(
        [{**asset, "prefab": "/Game/Other"}], lambda p: p, source) is None
    assert _client_preview([asset], lambda p: p, "activity:other") is None
    tab = {"role": "tab", "path": "/Game/Tab"}
    for shared in SHARED_ACTIVITY_BACKGROUNDS:
        background = {"role": "background",
                      "table": "Activity", "path": shared}
        assert _client_preview(
            [background, tab], lambda p: p, source) == "/Game/Tab"


@pytest.mark.parametrize("source,prefab,path", [
    ("activity:synthetic-a", "/Game/UiItem_Activity37POI",
     "/Game/Aki/UI/UIResources/Common/Image/BgCgBig/Activity/Activity37/Activity37POI/T_Activity37POIBg"),
    ("activity:synthetic-b", "/Game/UiItem_FarmGoldMain",
     "/Game/Aki/UI/UIResources/Common/Image/BgCgBig/Activity/ActivityFarmGold/T_ActivityFarmGold37"),
    ("activity:108700001", "/Game/UiView_RhythmShipLevel",
     "/Game/Aki/UI/UIResources/Common/Image/BgCgBig/Activity/Activity32/RhythmShip/T_RhythmShipLevelBg01"),
])
def test_single_published_prefab_background_does_not_prove_event_preview(source, prefab, path):
    from wuwa_story.events.domain import _client_preview

    candidate = {"role": "prefab_texture", "path": path, "prefab": prefab}
    assert _client_preview([candidate], lambda value: value, source) is None


@pytest.mark.asyncio
async def test_start_estimate_survives_source_and_projected_calendar_and_detail(event, repository, service, monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from fastapi.encoders import jsonable_encoder
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from wuwa_story.db.models.game_events import GameEventOccurrence
    from wuwa_story.db.repositories.event_browsing import _summary_source
    from wuwa_story.read_models.readers import events as projected
    from wuwa_story.read_models.source_sections import _calendar_payload

    estimate = {"basis": "previous_version_banner_end",
                "occurrence_id": 99, "version": "3.7", "server": "europe"}
    run = EventOccurrence(id=42, game_version="3.8", starts_at=datetime(2026, 11, 11, tzinfo=UTC),
                          ends_at=None, season=None, banner_path=None, source_data={"start_estimate": estimate})
    repository.runs.return_value = [EventRun(event, run)]
    source = (await service.browse("europe", summary=True))["events"][0]
    assert source["start_estimate"] == estimate and source["schedule_status"] == "estimated"
    detail = await service.detail(7, "europe", occurrence_id=42)
    assert detail["start_estimate"] == estimate
    payload = _calendar_payload(jsonable_encoder(
        {"event": source, "occurrences": [{**source, "server": "europe"}]}))
    monkeypatch.setattr(projected.entities, "_scope_entries", AsyncMock(
        return_value=[SimpleNamespace(entity_id=7, payload=payload)]))
    monkeypatch.setattr(projected.entities, "page_payload",
                        AsyncMock(return_value=payload))
    assert (await projected.events_page(None, "europe", summary=True))["events"][0]["start_estimate"] == estimate
    assert (await projected.event_detail(None, 7, "europe", 42))["start_estimate"] == estimate
    summary_query = select(_summary_source(GameEventOccurrence.source_data, "en")).compile(
        dialect=postgresql.dialect())
    assert "start_estimate" in summary_query.params.values()


@pytest.mark.asyncio
async def test_reverb_notice_is_visible_on_matching_undated_event(event, repository, service):
    event.source_data["client"].update({
        "gacha_type": 14,
        "locales": {"en": {"title": "Red Spring · Weapon Convene", "featured_names": ["Red Spring"]}},
    })
    repository.runs.return_value = [EventRun(event, None)]
    repository.announcements.return_value = [EventAnnouncement(
        43, "tideforge", "Tideforge Reverbs II", [], None,
        {"title": "[Tideforge Reverbs Ⅱ] Reverb Weapon Convene", "text":
         "During the event, selectable 5-Star Weapons: Red Spring, Tragicomedy, Luminous Hymn, Unflickering Valor, and 4-Star Weapons: Commando of Conviction, Variation, Jinzhou Keeper receive boosted drop rates!"},
    )]
    payload = await service.detail(7, "europe")
    assert [notice["id"] for notice in payload["notices"]] == [43]
    assert payload["starts_at"] is None and payload["ends_at"] is None


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
        EventNoticeSnapshot(
            11, "shared", {"banner_url": "https://official/old.png"}),
        EventNoticeSnapshot(
            99, "shared", {"banner_url": "https://official/other.png"}),
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
    session = AsyncMock()
    session.scalars.return_value = ["3.7.0"]
    session.get.return_value.complete = True
    app.dependency_overrides[game_events.get_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/events?server=asia&kind=banner&version=3.7&locale=fr&summary=true"
        )
        assert response.status_code == 200 and response.json() == {
            "events": []}
        missing = await client.get("/events/7/detail?server=america&occurrence_id=42&locale=fr")
    service.browse.assert_awaited_once_with(
        "asia", "banner", "3.7.0", "fr", summary=True)
    service.detail.assert_awaited_once_with(7, "america", 42, "fr")
    assert missing.status_code == 404 and missing.json() == {
        "detail": "Event run not found"}
