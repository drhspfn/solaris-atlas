from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from wuwa_story.ingestion.game_events import CLIENT_SOURCE, SOURCE
from wuwa_story.ingestion.recurring_events import recurring_schedule_rows


def test_recurring_import_targets_migrated_identity_and_preserves_history():
    matrix = SimpleNamespace(id=851, source=CLIENT_SOURCE, source_id="activity:100770002",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100770002",
                                            "client": {"asset_version": "3.8.0", "download_id": "current"}})
    ship = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    document = {"list": [{"id": 3, "child": [
        {"id": 10077000207, "sourceId": 100770002, "ver": ["3.8"], "season": {"start": 7},
         "time": [["2026-11-18T20:00:00Z", "2026-12-21T19:59:59.999Z"],
                  ["2026-11-19T03:00:00Z", "2026-12-21T19:59:59.999Z"],
                  ["2026-11-19T09:00:00Z", "2026-12-21T19:59:59.999Z"]]},
        {"id": 10039000102, "sourceId": 100390001, "ver": ["all"],
         "season": {"start": 2, "cycle": {"weeks": 4}}, "time": [["2025-03-16T20:00:00Z"]]},
        {"id": 777, "sourceId": 123, "time": [["2026-01-01T00:00:00Z"]]},
    ]}]}
    existing = [SimpleNamespace(event_id=853, server="asia", source_occurrence_id="10039000102",
        starts_at=datetime(2025, 3, 16, 20, tzinfo=UTC), ends_at=None, game_version=None,
        season={"start": 2, "cycle": {"weeks": 4}}, source_data={
            "time": ["2025-03-16T20:00:00Z"], "versions": ["all"], "schedule_source": SOURCE,
            "schedule_revision": "a" * 40,
            "schedule_url": f"https://raw.githubusercontent.com/{SOURCE}/{'a' * 40}/events.json"})]
    rows = recurring_schedule_rows(document, [matrix, ship], existing, "a" * 40)
    assert len(rows) == 3
    assert {row["event_id"] for row in rows} == {851}
    assert {row["server"] for row in rows} == {"asia", "europe", "america"}
    assert all(row["game_version"] == "3.8" and row["season"] == {"start": 7} for row in rows)
    assert all(row["source_data"]["schedule_source"] == SOURCE and
               row["source_data"]["schedule_revision"] == "a" * 40 for row in rows)
    assert all(row["source_data"]["client"]["download_id"] == "current" for row in rows)
    persisted = [SimpleNamespace(**row) for row in rows]
    assert recurring_schedule_rows(document, [matrix, ship], existing + persisted, "a" * 40) == []
    matrix.source_data["legacy_source_id"] = "3:999"
    assert recurring_schedule_rows(document, [matrix, ship], existing, "a" * 40) == []


@pytest.mark.asyncio
async def test_import_dry_run_does_not_write_and_apply_uses_conflict_guard(monkeypatch):
    from wuwa_story.ingestion import recurring_events

    event = SimpleNamespace(id=851, source=CLIENT_SOURCE, source_id="activity:100770002",
                            event_kind="recurring", source_data={})
    document = {"list": [{"id": 3, "child": [{"id": 10077000207, "sourceId": 100770002,
        "ver": ["3.8"], "time": [["2026-11-18T20:00:00Z", "2026-12-21T19:59:59.999Z"]]}]}]}
    client = AsyncMock()
    client.get.side_effect = [httpx.Response(200, json=body, request=httpx.Request("GET", "https://example.test"))
                              for body in ({"sha": "a" * 40}, document) * 2]
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(recurring_events.httpx, "AsyncClient", Mock(return_value=context))
    session = AsyncMock()
    session.scalars.side_effect = [[event], [], [event], []]
    dry = await recurring_events.import_recurring_schedules(session)
    assert len(dry["occurrences"]) == 1
    session.execute.assert_not_awaited()
    applied = await recurring_events.import_recurring_schedules(session, apply=True)
    assert len(applied["occurrences"]) == 1
    assert "ON CONFLICT" in str(session.execute.await_args_list[-1].args[0])


@pytest.mark.parametrize("suffix", ["", "@2026-11-19T03:00:00+00:00"])
def test_corrected_phase_updates_existing_identity_and_keeps_other_phases(suffix):
    event = SimpleNamespace(id=851, source=CLIENT_SOURCE, source_id="activity:100770002",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100770002"})
    old = SimpleNamespace(event_id=851, server="europe", source_occurrence_id="10077000207" + suffix,
        starts_at=datetime(2026, 11, 19, 3, tzinfo=UTC), ends_at=datetime(2026, 12, 21, tzinfo=UTC),
        game_version="3.8", season={"start": 7}, source_data={"client": {"download_id": "pinned"}})
    earlier = SimpleNamespace(event_id=851, server="europe", source_occurrence_id="10077000206",
        starts_at=datetime(2026, 10, 7, tzinfo=UTC), source_data={})
    item = {"id": 10077000207, "sourceId": 100770002, "ver": ["3.8"], "season": {"start": 7},
            "time": [[], ["2026-11-19T04:00:00Z", "2026-12-22T00:00:00Z"]]}
    document = {"list": [{"id": 3, "child": [item]}]}
    rows = recurring_schedule_rows(document, [event], [old, earlier], "b" * 40)
    assert len(rows) == 1 and rows[0]["source_occurrence_id"] == old.source_occurrence_id
    assert rows[0]["starts_at"].hour == 4 and rows[0]["ends_at"].day == 22
    assert rows[0]["source_data"]["client"] == {"download_id": "pinned"}
    persisted = SimpleNamespace(**rows[0])
    assert recurring_schedule_rows(document, [event], [persisted, earlier], "b" * 40) == []
    item["time"][1][1] = "2026-12-23T00:00:00Z"
    assert recurring_schedule_rows(document, [event], [persisted, earlier], "c" * 40)[0]["ends_at"].day == 23
    assert earlier.source_occurrence_id == "10077000206"
    persisted.source_data["notice_id"] = 12
    assert recurring_schedule_rows(document, [event], [persisted, earlier], "c" * 40) == []
    persisted.source_data.pop("notice_id")
    duplicate = SimpleNamespace(**{**persisted.__dict__, "source_occurrence_id": "10077000207@different"})
    assert recurring_schedule_rows(document, [event], [persisted, duplicate], "c" * 40) == []
