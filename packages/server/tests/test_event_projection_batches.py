"""Bounded source reads must render the same per-period payload as individual reads."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.db.repositories.event_browsing import read_event_runs
from wuwa_story.events.domain import CLIENT_SOURCE, Event, EventMedia, EventOccurrence, EventRun
from wuwa_story.events.repository import EVENT_RUN_BATCH_SIZE, EventRepository
from wuwa_story.events.service import EventService
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.source_sections import _request_caches


@pytest.mark.asyncio
async def test_batch_renderer_preserves_exact_per_period_media_and_manual_dates():
    event = Event(7, "event", "activity:7", "Current", None, "limited", None, [], None,
                  "https://official", {"client": {"download_id": "current"}}, canonical_event_id=3)
    runs = [EventRun(event, EventOccurrence(
        1, "3.7", datetime(1900, 1, 1, tzinfo=UTC), None, None, "UI/banner.png",
        {"client": {"download_id": "old"}, "schedule_source": "manual"})),
        EventRun(event, EventOccurrence(2, None, None, None, None, "capture/UI/banner.png",
                                       {"client": {"download_id": "old"}}))]
    repository = AsyncMock(spec=EventRepository)
    repository.runs.side_effect = lambda **kw: (
        runs if kw.get("occurrence_ids") is not None else
        [run for run in runs if run.occurrence.id == kw["occurrence_id"]])
    for name in ("announcements", "notice_snapshots", "reward_nodes", "spine"):
        getattr(repository, name).return_value = []
    repository.media.return_value = [EventMedia("capture/UI/banner.png", "historical", {"download_id": "old"})]
    service = EventService(repository, lambda key: "https://cdn/" + key, lambda value: value)
    individual = [(await service.browse("europe", event_id=7, occurrence_id=i))["events"][0]
                  for i in (1, 2)]
    repository.reset_mock()
    batched = await service.browse("europe", event_id=7, occurrence_ids=[1, 2])
    assert batched["events"] == individual
    assert batched["events"][0]["starts_at"].year == 1900
    assert batched["events"][1]["starts_at"] is None
    assert all(row["canonical_event_id"] == 3 for row in batched["events"])
    assert all(row["banner_url"] == "https://cdn/historical" for row in batched["events"])
    repository.announcements.assert_awaited_once()
    repository.media.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_batch_reuses_one_header_across_servers_and_bounds_selected_columns():
    record = GameEvent(id=7, source=CLIENT_SOURCE, source_id="activity:7", title="Event",
                       source_data={"client": {"download_id": "current"}, "large_header": "x" * 100000})
    occurrence = {field.key: None for field in GameEventOccurrence.__table__.columns}
    occurrence.update(id=42, event_id=7, server="europe", source_data={}, source_occurrence_id="manual:42")
    row = tuple(occurrence.values())
    session = SimpleNamespace(info={}, get=AsyncMock(return_value=record),
                              scalar=AsyncMock(return_value=None),
                              execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [row])))
    with source_build(), _request_caches(session):
        for server in ("europe", "asia", "america"):
            result = await read_event_runs(session, server=server, event_id=7, occurrence_ids=[42])
            assert result[0][0] is record
        await read_event_runs(session, server="", event_id=7)
        session.get.assert_awaited_once_with(GameEvent, 7)
        assert len(session.info["story_event_batch_headers"]) == 1
    assert session.info == {}
    for call in session.execute.await_args_list:
        statement = call.args[0]
        assert all(not column.shares_lineage(GameEvent.source_data) for column in statement.selected_columns)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "game_event_occurrence.id IN" in sql
        assert "game_event_occurrence.server =" in sql
    # A later locale/entity build must not reuse a previous selected/current header.
    with source_build(), _request_caches(session):
        await read_event_runs(session, server="asia", event_id=7, occurrence_ids=[42])
    assert session.get.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], list(range(EVENT_RUN_BATCH_SIZE + 1))])
async def test_unbounded_or_empty_batches_fail_before_sql(ids):
    session = SimpleNamespace(info={}, execute=AsyncMock(), get=AsyncMock())
    with pytest.raises(ValueError, match="1..16"):
        await read_event_runs(session, server="asia", event_id=7, occurrence_ids=ids)
    session.get.assert_not_awaited()
    session.execute.assert_not_awaited()
