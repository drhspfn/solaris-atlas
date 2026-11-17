from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.encoders import jsonable_encoder

from wuwa_story.events.domain import Event, EventOccurrence, EventRun, _notice_schedule_evidence
from wuwa_story.events.repository import EventRepository
from wuwa_story.events.service import EventService
from wuwa_story.read_models.readers import events as reader
from wuwa_story.read_models.source_sections import _calendar_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(("captured", "expected"), [
    (False, "current_catalog_fallback"),
    (True, "occurrence_snapshot"),
])
async def test_catalog_provenance_never_assigns_a_schedule_version(monkeypatch, captured, expected):
    client = {"asset_version": "3.8.0", "download_id": "same", "locales": {"en": {"title": "Event"}}}
    event = Event(7, None, "activity:7", "Event", None, "limited", None, [], None,
                  "https://example.test", {"client": client})
    occurrence = EventOccurrence(8, None, datetime(2025, 2, 3, tzinfo=UTC),
        datetime(2025, 3, 3, tzinfo=UTC), None, None, {"client": client} if captured else {})
    repo = AsyncMock(spec=EventRepository)
    repo.runs.return_value = [EventRun(event, occurrence)]
    for method in ("announcements", "notice_snapshots", "reward_nodes", "media", "spine"):
        getattr(repo, method).return_value = []
    service = EventService(repo, lambda value: value, lambda value: value)
    source = (await service.browse("europe", summary=True))["events"][0]
    detail = await service.detail(7, "europe", 8)
    assert source["catalog_version"] == "3.8.0" and source["catalog_provenance"] == expected
    assert source["game_version"] is None and detail["game_version"] is None
    assert detail["catalog_provenance"] == expected
    payload = _calendar_payload(jsonable_encoder({"event": source, "occurrences": [{**source, "server": "europe"}]}))
    monkeypatch.setattr(reader.entities, "_scope_entries", AsyncMock(return_value=[SimpleNamespace(entity_id=7, payload=payload)]))
    projected = (await reader.events_page(None, "europe", summary=True))["events"][0]
    assert projected["catalog_provenance"] == expected and projected["game_version"] is None


def test_only_pinned_schedule_notice_yields_update_version_evidence():
    data = {"notice_id": 36, "notice_revision": "revision", "schedule_revision": "revision",
            "notice_presentation": {"en": {"description":
                "✦Duration✦\nVersion 3.8 update - 2026-12-03 09:59 (server time)\n✦Rules✦"}}}
    occurrence = SimpleNamespace(source_data=data)
    assert _notice_schedule_evidence(occurrence) == {
        "notice_id": 36, "notice_revision": "revision", "update_version": "3.8"}
    data["schedule_revision"] = "different"
    assert _notice_schedule_evidence(occurrence) is None
    data["schedule_revision"] = "revision"
    data["notice_presentation"]["en"]["description"] = "Version 3.8 update elsewhere; ✦Duration✦ unknown ✦Rules✦"
    assert _notice_schedule_evidence(occurrence) is None


@pytest.mark.asyncio
async def test_notice_update_evidence_survives_source_and_projection_without_changing_dates(monkeypatch):
    client = {"asset_version": "3.8.0", "download_id": "same", "locales": {"en": {"title": "Lily"}}}
    event = Event(1945, None, "gacha:100090", "Lily", None, "banner", None, [], None,
                  "https://example.test", {"client": client})
    data = {"client": client, "notice_id": 36, "notice_revision": "revision",
            "schedule_revision": "revision", "start_estimate": {"basis": "previous_version_banner_end"},
            "notice_presentation": {"en": {"description":
                "✦Duration✦\nVersion 3.8 update - 2026-12-03 09:59 (server time)\n✦Rules✦"}}}
    start = datetime(2026, 11, 11, 10, 59, 59, 999000, tzinfo=UTC)
    end = datetime(2026, 12, 3, 8, 59, tzinfo=UTC)
    occurrence = EventOccurrence(6288, None, start, end, None, None, data)
    repo = AsyncMock(spec=EventRepository)
    repo.runs.return_value = [EventRun(event, occurrence)]
    for method in ("announcements", "notice_snapshots", "reward_nodes", "media", "spine"):
        getattr(repo, method).return_value = []
    service = EventService(repo, lambda value: value, lambda value: value)
    source = (await service.browse("europe", summary=True))["events"][0]
    assert source["game_version"] is None and source["starts_at"] == start and source["ends_at"] == end
    assert source["schedule_status"] == "estimated"
    assert source["schedule_evidence"] == {"notice_id": 36, "notice_revision": "revision", "update_version": "3.8"}
    payload = _calendar_payload(jsonable_encoder({"event": source, "occurrences": [{**source, "server": "europe"}]}))
    monkeypatch.setattr(reader.entities, "_scope_entries", AsyncMock(return_value=[SimpleNamespace(entity_id=1945, payload=payload)]))
    projected = (await reader.events_page(None, "europe", summary=True))["events"][0]
    assert projected["schedule_evidence"] == source["schedule_evidence"]
    assert projected["game_version"] is None and projected["starts_at"] == start.isoformat()


def test_summary_query_retains_schedule_revision_for_evidence():
    from sqlalchemy.dialects import postgresql

    from wuwa_story.db.models.game_events import GameEventOccurrence
    from wuwa_story.db.repositories.event_browsing import _summary_source

    compiled = _summary_source(GameEventOccurrence.source_data, "en").compile(dialect=postgresql.dialect())
    assert "schedule_revision" in compiled.params.values()
