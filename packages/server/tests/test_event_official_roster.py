from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.encoders import jsonable_encoder

from wuwa_story.events.domain import Event, EventOccurrence, EventRun, _presentation
from wuwa_story.events.repository import EventRepository
from wuwa_story.events.service import EventService
from wuwa_story.read_models.readers import events as reader
from wuwa_story.read_models.source_sections import _calendar_payload


def fixture_run(text):
    client = {"download_id": "client-38", "asset_version": "3.8.0", "locales": {"en": {
        "title": "Lily · Resonator Convene", "featured_names": ["Lily"],
        "up_names": ["Sanhua", "Baizhi", "Chixia"]}}}
    event = Event(1945, None, "gacha:100090", "Lily", None, "banner", None, [], None,
                  "https://example.test", {"client": client})
    occurrence = EventOccurrence(6288, None, datetime(2026, 11, 11, tzinfo=UTC),
        datetime(2026, 12, 3, tzinfo=UTC), None, None,
        {"client": client, "notice_id": 36, "notice_revision": "a" * 64,
         "notice_presentation": {"en": {"title": "[Bloom of Yesterday's Dreamsoil]",
                                         "description": text}}})
    return EventRun(event, occurrence)


@pytest.mark.asyncio
async def test_notice_run_roster_preserves_client_and_matches_source_detail_and_projection(monkeypatch):
    run = fixture_run("During the event, 5-Star Resonator: Lily, 4-Star Resonators: Youhu, Aalto, and Yangyang receive boosted drop rates!")
    repo = AsyncMock(spec=EventRepository)
    repo.runs.return_value = [run]
    for method in ("announcements", "notice_snapshots", "reward_nodes", "media", "spine"):
        getattr(repo, method).return_value = []
    service = EventService(repo, lambda value: value, lambda value: value)
    listing = (await service.browse("europe", summary=True))["events"][0]
    detail = await service.detail(1945, "europe", 6288)
    assert listing["up_names"] == detail["up_names"] == ["Youhu", "Aalto", "Yangyang"]
    assert listing["client_up_names"] == detail["client_up_names"] == ["Sanhua", "Baizhi", "Chixia"]
    assert listing["up_names_source"] == detail["up_names_source"] == "notice"
    assert run.occurrence.source_data["client"]["locales"]["en"]["up_names"] == ["Sanhua", "Baizhi", "Chixia"]
    payload = _calendar_payload(jsonable_encoder({"event": listing, "occurrences": [{**listing, "server": "europe"}]}))
    monkeypatch.setattr(reader.entities, "_scope_entries", AsyncMock(return_value=[SimpleNamespace(entity_id=1945, payload=payload)]))
    projected = (await reader.events_page(None, "europe", summary=True))["events"][0]
    assert projected["up_names"] == listing["up_names"]
    assert projected["client_up_names"] == listing["client_up_names"]
    assert projected["up_names_source"] == "notice"


@pytest.mark.parametrize("text", [
    "4-Star Resonators: Youhu, Aalto and Yangyang receive boosted drop rates!",
    "4-Star Resonators: Youhu, Youhu, and Yangyang receive boosted drop rates!",
    "Resonators Youhu, Aalto, and Yangyang may be featured.",
])
def test_ambiguous_or_unstated_roster_keeps_client_evidence(text):
    run = fixture_run(text)
    result = _presentation(run.event, run.occurrence, "en")
    assert result["up_names"] == ["Sanhua", "Baizhi", "Chixia"]
    assert "up_names_source" not in result
