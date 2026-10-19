from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.ingestion.client_events import store_client_events
from wuwa_story.ingestion.game_events import CLIENT_SOURCE, SOURCE


def record(title="Client title"):
    return {"event_kind": "limited", "banner_path": "/Game/Actual",
            "artwork": [{"path": "/Game/Actual", "role": "background"}], "resources": [],
            "locales": {"en": {"title": title, "description": "Actual description"}}}


@pytest.mark.asyncio
async def test_client_import_adds_unlisted_events_and_preserves_exact_historical_dates():
    old = SimpleNamespace(id=7, source=SOURCE,
                          source_id="2:100", source_data={"item": "legacy"})
    occurrence = SimpleNamespace(event_id=7, server="europe", starts_at="preserved date",
                                 game_version="3.7", source_data={"time": ["preserved date"]})
    session = AsyncMock()
    session.scalars.side_effect = [[old], [occurrence]]
    added = []
    session.add = added.append

    async def flush():
        for index, row in enumerate(added):
            if isinstance(row, GameEvent) and row.id is None:
                row.id = 1000 + index
    session.flush.side_effect = flush

    result = await store_client_events(session, {"100": record(), "101": record("Unlisted event")},
                                       "3.7.0", "a" * 64)

    assert result["events"] == 2
    assert old.source == CLIENT_SOURCE and old.source_id == "activity:100"
    assert occurrence.starts_at == "preserved date"
    assert occurrence.source_data["time"] == ["preserved date"]
    assert old.title == "Client title" and old.banner_path == "/Game/Actual"
    created = [row for row in added if isinstance(row, GameEvent)]
    assert created[0].source_id == "activity:101"
    assert len([row for row in added if isinstance(
        row, GameEventOccurrence)]) == 5
    assert result["artwork_paths"] == ["/Game/Actual"]


@pytest.mark.asyncio
async def test_reimport_reuses_client_identity_and_retains_discovered_prefab_images():
    image = {"path": "/Game/FromPrefab", "role": "prefab_texture"}
    event = SimpleNamespace(id=7, source=CLIENT_SOURCE, source_id="activity:100",
                            source_data={"client": {"download_id": "a" * 64, "prefab_artwork": [image]}})
    rows = [SimpleNamespace(event_id=7, server=server, source_data={})
            for server in ["asia", "europe", "america"]]
    session = AsyncMock()
    session.scalars.side_effect = [[event], rows]
    session.add = lambda _: pytest.fail(
        "reimport must not duplicate events or occurrences")
    await store_client_events(session, {"100": record()}, "3.7.0", "a" * 64)
    assert event.source_data["client"]["prefab_artwork"] == [image]
    assert rows[0].source_data["client"]["download_id"] == "a" * 64


@pytest.mark.asyncio
async def test_new_download_does_not_overwrite_dated_run_snapshots():
    old_client = {"download_id": "old", "locales": {"en": {"title": "First run"}},
                  "artwork": [{"path": "/Game/FirstRun"}]}
    event = SimpleNamespace(id=7, source=CLIENT_SOURCE, source_id="activity:100",
                            source_data={"client": old_client})
    rows = [SimpleNamespace(event_id=7, server=server, starts_at="2026-10-01",
                            banner_path="/Game/FirstRun", source_data={"client": old_client})
            for server in ["asia", "europe", "america"]]
    session = AsyncMock()
    session.scalars.side_effect = [[event], rows]
    session.add = lambda _: pytest.fail("must reuse canonical event and dated runs")
    await store_client_events(session, {"100": record("Rerun title")}, "3.7.1", "new")
    assert event.title == "Rerun title"
    for row in rows:
        assert row.banner_path == "/Game/FirstRun"
        assert row.source_data["client"] == old_client
