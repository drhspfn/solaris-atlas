from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from wuwa_story.ingestion.game_events import CLIENT_SOURCE, SOURCE
from wuwa_story.ingestion.recurring_events import calculated_cycles, recurring_schedule_rows


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
    session.scalars.side_effect = [[event], [], [event], [], []]
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


def test_declared_cycles_derive_current_and_next_without_rewriting_anchor():
    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    start = datetime(2025, 3, 17, 3, tzinfo=UTC)
    anchor = SimpleNamespace(id=2427, event_id=853, server="europe", source_occurrence_id="10039000102",
        starts_at=start, ends_at=None, game_version=None,
        season={"start": 2, "cycle": {"weeks": 4}}, source_data={"time": [start.isoformat()], "versions": ["all"]})
    now = datetime(2026, 11, 17, 12, tzinfo=UTC)
    rows, obsolete = calculated_cycles([event], [anchor], now)
    assert len(rows) == 2 and obsolete == []
    assert rows[0]["starts_at"] <= now < rows[0]["ends_at"]
    assert rows[1]["starts_at"] == rows[0]["ends_at"]
    assert rows[0]["season"]["start"] == 23
    assert all(row["game_version"] is None and row["source_data"]["start_estimate"]["end_is_estimated"] for row in rows)
    persisted = [SimpleNamespace(id=3000+i, **row) for i, row in enumerate(rows)]
    assert calculated_cycles([event], [anchor, *persisted], now) == ([], [])
    later = SimpleNamespace(id=42, event_id=853, server="europe", source_occurrence_id="override",
        starts_at=rows[1]["starts_at"] + timedelta(days=2), ends_at=None, game_version=None,
        season=None, source_data={})
    recalculated, obsolete = calculated_cycles([event], [anchor, *persisted, later], now)
    assert recalculated == [] and obsolete == [3001]
    assert anchor.id not in obsolete
    assert calculated_cycles([event], [anchor, later], later.starts_at + timedelta(days=60)) == ([], [])


def test_cycles_require_valid_trusted_anchored_rule():
    start = datetime(2025, 3, 17, tzinfo=UTC)
    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
                            event_kind="recurring", source_data={})
    anchor = SimpleNamespace(id=1, event_id=853, server="asia", source_occurrence_id="anchor",
        starts_at=start, ends_at=None, game_version=None, season={"start": 2, "cycle": {"weeks": 4}},
        source_data={"time": [start.isoformat()]})
    assert calculated_cycles([event], [anchor], start + timedelta(days=30)) == ([], [])
    anchor.source_data["schedule_source"] = SOURCE
    anchor.season["cycle"]["weeks"] = 0
    assert calculated_cycles([event], [anchor], start + timedelta(days=30)) == ([], [])


def test_future_recurrence_override_does_not_replace_current_anchor():
    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    start = datetime(2026, 1, 1, tzinfo=UTC)
    def anchor(identifier, at, weeks):
        return SimpleNamespace(id=identifier, event_id=853, server="asia", source_occurrence_id=str(identifier),
            starts_at=at, ends_at=None, game_version=None, season={"start": 2, "cycle": {"weeks": weeks}},
            source_data={"time": [at.isoformat()]})
    old = anchor(1, start, 4)
    future = anchor(2, start + timedelta(days=50), 2)
    now = start + timedelta(days=35)
    rows, _ = calculated_cycles([event], [old, future], now)
    assert len(rows) == 0  # The current old interval would overlap the explicit future override.
    rows, _ = calculated_cycles([event], [old, future], future.starts_at + timedelta(days=1))
    assert len(rows) == 1 and rows[0]["source_data"]["start_estimate"]["occurrence_id"] == 2


def test_anchor_correction_supersedes_unexpired_derived_windows():
    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    start = datetime(2025, 3, 17, 3, tzinfo=UTC)
    anchor = SimpleNamespace(id=2427, event_id=853, server="europe", source_occurrence_id="10039000102",
        starts_at=start, ends_at=None, game_version=None, season={"start": 2, "cycle": {"weeks": 4}},
        source_data={"time": [start.isoformat()], "schedule_source": SOURCE, "schedule_revision": "a"})
    now = datetime(2026, 11, 17, tzinfo=UTC)
    old, _ = calculated_cycles([event], [anchor], now)
    existing = [SimpleNamespace(id=3000+i, **row) for i, row in enumerate(old)]
    anchor.starts_at += timedelta(hours=1)
    anchor.source_data = {"time": [anchor.starts_at.isoformat()], "schedule_source": SOURCE, "schedule_revision": "b"}
    updated, obsolete = calculated_cycles([event], [anchor, *existing], now)
    assert obsolete == [3000, 3001] and len(updated) == 2
    assert updated[0]["starts_at"] == old[0]["starts_at"] + timedelta(hours=1)
    assert all(row["source_data"]["schedule_revision"] == "b" for row in updated)
    assert anchor.id not in obsolete


def test_anchor_end_correction_preserves_current_and_supersedes_next():
    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    start = datetime(2025, 3, 17, 3, tzinfo=UTC)
    anchor = SimpleNamespace(id=2427, event_id=853, server="europe", source_occurrence_id="10039000102",
        starts_at=start, ends_at=None, game_version=None, season={"start": 2, "cycle": {"weeks": 4}},
        source_data={"time": [start.isoformat()], "schedule_source": SOURCE, "schedule_revision": "a"})
    now = datetime(2026, 11, 17, tzinfo=UTC)
    old, _ = calculated_cycles([event], [anchor], now)
    existing = [SimpleNamespace(id=3000+i, **row) for i, row in enumerate(old)]
    anchor.ends_at = old[1]["starts_at"] + timedelta(days=2)
    rows, obsolete = calculated_cycles([event], [anchor, *existing], now)
    assert rows == [] and obsolete == [3001]
    existing[1].source_data["notice_id"] = 47
    assert 3001 not in calculated_cycles([event], [anchor, *existing], now)[1]


@pytest.mark.parametrize(("server", "hours"), [("asia", 8), ("europe", 1), ("america", -5)])
def test_declared_cycle_boundaries_use_server_anchor_timezone(server, hours):
    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    start = datetime(2025, 3, 17, 4, tzinfo=timezone(timedelta(hours=hours)))
    anchor = SimpleNamespace(id=1, event_id=853, server=server, source_occurrence_id="10039000102",
        starts_at=start, ends_at=None, game_version=None, season={"start": 2, "cycle": {"weeks": 4}},
        source_data={"time": [start.isoformat()]})
    now = datetime(2026, 11, 17, tzinfo=UTC)
    rows, _ = calculated_cycles([event], [anchor], now)
    assert len(rows) == 2 and rows[0]["season"]["start"] == 23
    assert all(row["starts_at"].hour == 4 and row["starts_at"].utcoffset() == timedelta(hours=hours)
               for row in rows)


def test_authored_phase_supersedes_calculated_even_at_same_start():
    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    start = datetime(2025, 3, 17, 3, tzinfo=UTC)
    anchor = SimpleNamespace(id=2427, event_id=853, server="europe", source_occurrence_id="10039000102",
        starts_at=start, ends_at=None, game_version=None, season={"start": 2, "cycle": {"weeks": 4}},
        source_data={"time": [start.isoformat()]})
    now = datetime(2026, 11, 17, tzinfo=UTC)
    old, _ = calculated_cycles([event], [anchor], now)
    derived = SimpleNamespace(id=3000, **old[0])
    item = {"id": 10039000123, "sourceId": 100390001, "season": {"start": 23}, "ver": ["3.8"],
            "time": [[], [old[0]["starts_at"].isoformat(), old[0]["ends_at"].isoformat()]]}
    authored = recurring_schedule_rows({"list": [{"id": 3, "child": [item]}]}, [event], [anchor, derived], "b" * 40)
    assert len(authored) == 1 and authored[0]["source_occurrence_id"] == "10039000123"
    authored_row = SimpleNamespace(id=4000, **authored[0])
    planned, obsolete = calculated_cycles([event], [anchor, derived, authored_row], now)
    assert 3000 in obsolete and all(row["season"]["start"] != 23 for row in planned)
    authored_row.source_data["notice_id"] = 99
    assert 4000 not in calculated_cycles([event], [anchor, derived, authored_row], now)[1]


@pytest.mark.asyncio
async def test_notice_import_recalculates_after_correcting_stable_anchor(monkeypatch):
    from wuwa_story.ingestion import recurring_events

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 17, tzinfo=tz)

    event = SimpleNamespace(id=853, source=CLIENT_SOURCE, source_id="activity:100390001",
        event_kind="recurring", source_data={"legacy_source": SOURCE, "legacy_source_id": "3:100390001"})
    start = datetime(2025, 3, 17, 3, tzinfo=UTC)
    anchor = SimpleNamespace(id=2427, event_id=853, server="europe", source_occurrence_id="10039000102",
        starts_at=start, ends_at=None, game_version=None, season={"start": 2, "cycle": {"weeks": 4}},
        source_data={"time": [start.isoformat()], "versions": ["all"], "schedule_source": SOURCE, "schedule_revision": "a"})
    old, _ = calculated_cycles([event], [anchor], FixedDatetime.now(UTC))
    existing = [SimpleNamespace(id=3000+i, **row) for i, row in enumerate(old)]
    corrected_start = start + timedelta(hours=1)
    revised = SimpleNamespace(**{**anchor.__dict__, "starts_at": corrected_start,
        "source_data": {"time": [corrected_start.isoformat()], "versions": ["all"],
                        "schedule_source": SOURCE, "schedule_revision": "b" * 40}})
    document = {"list": [{"id": 3, "child": [{"id": 10039000102, "sourceId": 100390001,
        "ver": ["all"], "season": anchor.season, "time": [[], [corrected_start.isoformat()]]}]}]}
    client = AsyncMock()
    client.get.side_effect = [httpx.Response(200, json=body, request=httpx.Request("GET", "https://example.test"))
                              for body in ({"sha": "b" * 40}, document)]
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(recurring_events.httpx, "AsyncClient", Mock(return_value=context))
    monkeypatch.setattr(recurring_events, "datetime", FixedDatetime)
    session = AsyncMock()
    session.scalars.side_effect = [[event], [anchor, *existing], [revised, *existing]]
    result = await recurring_events.import_recurring_schedules(session, apply=True)
    assert result["occurrences"][0]["source_occurrence_id"] == "10039000102"
    assert result["superseded_cycle_ids"] == [3000, 3001]
    assert len(result["calculated_cycles"]) == 2
    assert all(row["source_data"]["schedule_revision"] == "b" * 40 for row in result["calculated_cycles"])
    assert any("DELETE" in str(call.args[0]) for call in session.execute.await_args_list)
