from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from wuwa_story.api.routes.game_events import _presentation
from wuwa_story.ingestion import game_events
from wuwa_story.ingestion.game_events import _deduplicate_occurrences, _title


def test_deduplicate_occurrences_keeps_latest_duplicate_and_distinct_servers():
    rows = [
        {
            "event_id": 1,
            "source_occurrence_id": "100",
            "server": "asia",
            "source_data": {"revision": 1},
        },
        {
            "event_id": 1,
            "source_occurrence_id": "100",
            "server": "europe",
            "source_data": {"revision": 1},
        },
        {
            "event_id": 1,
            "source_occurrence_id": "100",
            "server": "asia",
            "source_data": {"revision": 2},
        },
        {
            "event_id": 2,
            "source_occurrence_id": "100",
            "server": "asia",
            "source_data": {"revision": 1},
        },
    ]

    result = _deduplicate_occurrences(rows)

    assert result == [rows[2], rows[1], rows[3]]


def test_known_recurring_modes_have_reader_facing_names():
    assert _title("events/matrix", None, "10077000206") == "Endstate Matrix"
    assert _title("events/whimperingwastes", None, "10039000102") == "Whimpering Wastes"


def test_unnamed_activity_uses_a_reader_label_instead_of_an_internal_id():
    assert _title(None, None, "2") == "Limited-time event"


@pytest.mark.asyncio
async def test_timetable_reimport_preserves_client_titles_and_occurrence_artwork(monkeypatch):
    client_data = {"asset_version": "3.7.0", "banner_path": "/Game/RealBanner",
                   "locales": {"en": {"title": "Actual title"}}}
    event = SimpleNamespace(id=9, source_id="2:102000009", title="Actual title",
                            description="Actual description", source_data={"client": client_data})
    occurrence = SimpleNamespace(event_id=9, source_occurrence_id="102000009", server="asia",
                                 game_version="3.7", source_data={"client": client_data})
    session = AsyncMock()
    session.scalars.side_effect = [[event], [occurrence]]
    identities = Mock()
    identities.all.return_value = [(event.source_id, event.id)]
    session.execute.side_effect = [None, identities, identities, None, None]
    client = AsyncMock()
    client.get.side_effect = [httpx.Response(200, json=document,
                                            request=httpx.Request("GET", "https://example.test"))
                              for document in [
                                  {"sha": "a" * 40},
                                  {"list": [{"id": 2, "child": [{"id": 102000009,
                                      "ver": ["3.7"], "time": [["2026-10-22T00:00:00Z",
                                                                   "2026-11-11T00:00:00Z"]]}]}]},
                                  [],
                              ]]
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(game_events.httpx, "AsyncClient", Mock(return_value=context))

    result = await game_events.import_game_events(session)

    assert result["events"] == 1
    event_insert = session.execute.await_args_list[0].args[0].compile().params
    assert event_insert["title_m0"] == "Actual title"
    assert event_insert["banner_path_m0"] == "/Game/RealBanner"
    occurrence_insert = session.execute.await_args_list[-1].args[0].compile().params
    assert occurrence_insert["source_data_m0"]["client"] == client_data
    assert occurrence_insert["banner_path_m0"] == "/Game/RealBanner"


def test_occurrence_localization_precedes_event_and_falls_back_to_english():
    event = SimpleNamespace(source_data={"client": {"locales": {"en": {"title": "Old title"}}}})
    occurrence = SimpleNamespace(source_data={"client": {"locales": {
        "en": {"title": "Actual title"}, "ru": {"title": "Название"},
    }}})
    assert _presentation(event, occurrence, "ru")["title"] == "Название"
    assert _presentation(event, occurrence, "fr")["title"] == "Actual title"
