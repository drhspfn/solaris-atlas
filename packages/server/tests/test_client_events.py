from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
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
    session.get.return_value = None
    session.scalars.side_effect = [[old], [occurrence]]
    added = []
    session.add = added.append
    session.add_all = added.extend

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
    assert "client" not in occurrence.source_data
    assert old.title == "Client title" and old.banner_path == "/Game/Actual"
    created = [row for row in added if isinstance(row, GameEvent)]
    assert created[0].source_id == "activity:101"
    assert len([row for row in added if isinstance(
        row, GameEventOccurrence)]) == 6  # The dated row has no matching client capture.
    assert result["artwork_paths"] == ["/Game/Actual"]
    assert [(row.version, row.download_id, row.complete) for row in added if isinstance(row, GameEventSnapshot)] == [
        ("3.7.0", "a" * 64, True)
    ]
    assert {row.event_id for row in added if isinstance(row, GameEventSnapshotMember)} == {7, created[0].id}


@pytest.mark.asyncio
async def test_client_import_preserves_manual_unknown_start_and_locks_occurrences():
    event = SimpleNamespace(id=7, source=CLIENT_SOURCE, source_id="activity:100", source_data={})
    manual = SimpleNamespace(event_id=7, server="europe", starts_at=None, ends_at="manual end",
                             game_version="3.7", source_data={"schedule_source": "manual"})
    session = AsyncMock()
    session.get.return_value = None
    session.scalars.side_effect = [[event], [manual]]
    session.add = lambda row: None
    session.add_all = lambda rows: None
    await store_client_events(session, {"100": record()}, "3.7.0", "a" * 64)
    assert manual.starts_at is None and manual.ends_at == "manual end"
    assert manual.source_data["schedule_source"] == "manual"
    assert "FOR UPDATE" in str(session.scalars.await_args_list[1].args[0])


@pytest.mark.asyncio
async def test_reimport_reuses_client_identity_and_retains_discovered_prefab_images():
    image = {"path": "/Game/FromPrefab", "role": "prefab_texture"}
    event = SimpleNamespace(id=7, source=CLIENT_SOURCE, source_id="activity:100",
                            source_data={"client": {"download_id": "a" * 64, "prefab_artwork": [image],
                                                    "resources": record()["resources"],
                                                    "artwork": record()["artwork"]}})
    rows = [SimpleNamespace(event_id=7, server=server, source_data={})
            for server in ["asia", "europe", "america"]]
    session = AsyncMock()
    session.get.return_value = None
    session.scalars.side_effect = [[event], rows]
    added = []
    session.add = added.append
    session.add_all = added.extend
    await store_client_events(session, {"100": record()}, "3.7.0", "a" * 64)
    assert event.source_data["client"]["prefab_artwork"] == [image]
    assert rows[0].source_data["client"]["download_id"] == "a" * 64
    assert not any(isinstance(row, (GameEvent, GameEventOccurrence)) for row in added)


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
    session.get.return_value = None
    session.scalars.side_effect = [[event], rows]
    added = []
    session.add = added.append
    session.add_all = added.extend
    await store_client_events(session, {"100": record("Rerun title")}, "3.7.1", "new")
    assert event.title == "Rerun title"
    for row in rows:
        assert row.banner_path == "/Game/FirstRun"
        assert row.source_data["client"] == old_client
    assert not any(isinstance(row, GameEvent) for row in added)
    assert len([row for row in added if isinstance(row, GameEventOccurrence)]) == 3


@pytest.mark.asyncio
async def test_partial_import_does_not_publish_an_incomplete_snapshot():
    event = SimpleNamespace(id=7, source=CLIENT_SOURCE, source_id="activity:100", source_data={})
    rows = [SimpleNamespace(event_id=7, server=server, source_data={})
            for server in ["asia", "europe", "america"]]
    session = AsyncMock()
    session.get.return_value = None
    session.scalars.side_effect = [[event], rows]
    added = []
    session.add = added.append
    session.add_all = added.extend
    result = await store_client_events(session, {"100": record(), "101": {"locales": {}}}, "3.8.0", "new")
    assert result["unresolved"]
    assert [row.complete for row in added if isinstance(row, GameEventSnapshot)] == [False]
    assert [row.event_id for row in added if isinstance(row, GameEventSnapshotMember)] == [7]
    assert all("DELETE" not in str(call.args[0]) for call in session.execute.await_args_list)
