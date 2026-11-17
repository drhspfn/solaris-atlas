"""Versioned event presentation against a disposable PostgreSQL database."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import cast, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from test_admin_console import console_database as console_database
from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
from wuwa_story.db.repositories.event_browsing import _capture_enrichment, read_event_runs
from wuwa_story.events.domain import CLIENT_SOURCE

pytestmark = pytest.mark.skipif(
    not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Isolated PostgreSQL required"
)


@pytest.mark.asyncio
async def test_snapshot_capture_applies_to_events_and_periods_without_mutating_current(console_database):
    engine = create_async_engine(console_database, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    old = {
        "download_id": "capture-38",
        "asset_version": "3.8.0",
        "locales": {"en": {"title": "Old presentation"}},
        "artwork": [{"path": "/old", "role": "background"}],
        "full_only": "old full metadata",
    }
    current = {
        "download_id": "capture-39",
        "asset_version": "3.9.0",
        "locales": {"en": {"title": "Current presentation"}},
        "artwork": [{"path": "/current", "role": "background"}],
        "full_only": "current full metadata",
    }
    try:
        async with factory() as db:
            event = GameEvent(source=CLIENT_SOURCE, source_id="activity:capture-test", title="Event",
                              event_kind="limited", source_url="https://example.test",
                              source_revision="test", source_data={"client": current, "notice_id": 42})
            no_period = GameEvent(source=CLIENT_SOURCE, source_id="activity:capture-no-period", title="No period",
                                  event_kind="limited", source_url="https://example.test",
                                  source_revision="test", source_data={"client": current})
            db.add_all([event, no_period, GameEventSnapshot(version="3.8.0", download_id="capture-38", complete=True)])
            await db.flush()
            event_id, no_period_id = event.id, no_period.id
            await db.execute(text("INSERT INTO ops.read_projection_scope(kind,snapshot_key) "
                                  "VALUES ('game_event','3.8.0')"))
            db.add_all([
                GameEventSnapshotMember(version="3.8.0", event_id=event_id, client_capture=old),
                GameEventSnapshotMember(version="3.8.0", event_id=no_period_id, client_capture=old),
                GameEventOccurrence(event_id=event_id, source_occurrence_id="manual:known", server="europe",
                                    starts_at=datetime(2026, 11, 11, tzinfo=UTC),
                                    ends_at=datetime(2026, 12, 1, tzinfo=UTC),
                                    source_data={"client": current, "schedule_source": "manual"}),
                GameEventOccurrence(event_id=event_id, source_occurrence_id="manual:unknown", server="europe",
                                    starts_at=None, ends_at=datetime(2026, 12, 21, tzinfo=UTC),
                                    source_data={"client": current, "schedule_source": "manual"}),
            ])
            await db.commit()

        async with factory() as db:
            dirty = await db.scalars(text("SELECT snapshot_key FROM ops.read_projection_dirty "
                                          "WHERE kind='game_event' AND entity_id=:identity"), {"identity": event_id})
            assert {"current", "3.8.0"} <= set(dirty)
            discovered = {**old, "prefab_artwork": [{"path": "/old-discovered"}]}
            extra = await db.scalar(select(_capture_enrichment(cast(old, JSONB), cast(discovered, JSONB))))
            assert extra == {"prefab_artwork": discovered["prefab_artwork"]}
            for changed in ({"download_id": "different"}, {"resources": [{"path": "/different-root"}]}):
                assert await db.scalar(select(_capture_enrichment(
                    cast(old, JSONB), cast({**discovered, **changed}, JSONB)))) == {}
            for summary in (True, False):
                rows = await read_event_runs(db, server="europe", version="3.8.0", summary=summary)
                periods = [(event, occurrence) for event, occurrence in rows if event.id == event_id]
                assert len(periods) == 2
                assert {occurrence.source_occurrence_id for _, occurrence in periods} == {
                    "manual:known", "manual:unknown"
                }
                assert {occurrence.starts_at is None for _, occurrence in periods} == {True, False}
                for row_event, occurrence in periods:
                    for row in (row_event, occurrence):
                        assert row.source_data["client"]["download_id"] == "capture-38"
                        assert row.source_data["client"]["locales"]["en"]["title"] == "Old presentation"
                        assert row.source_data["client"]["artwork"] == old["artwork"]
                        if not summary:
                            assert row.source_data["client"] == old
                    assert occurrence.source_data["schedule_source"] == "manual"
                    assert row_event.source_data["notice_id"] == 42
                empty = [(row_event, occurrence) for row_event, occurrence in rows if row_event.id == no_period_id]
                assert len(empty) == 1 and empty[0][1] is None
                assert empty[0][0].source_data["client"]["download_id"] == "capture-38"
                assert empty[0][0].source_data["client"]["locales"]["en"]["title"] == "Old presentation"
                if not summary:
                    assert empty[0][0].source_data["client"] == old

            for summary in (True, False):
                rows = await read_event_runs(db, server="europe", summary=summary)
                for row_event, occurrence in rows:
                    assert row_event.source_data["client"]["download_id"] == "capture-39"
                    assert row_event.source_data["client"]["locales"]["en"]["title"] == "Current presentation"
                    assert row_event.source_data["client"]["artwork"] == current["artwork"]
                    if occurrence is not None:
                        assert occurrence.source_data["client"]["download_id"] == "capture-39"
                        assert occurrence.source_data["client"]["locales"]["en"]["title"] == "Current presentation"

        # Re-read persisted JSON in a fresh session to exclude identity-map effects.
        async with factory() as db:
            persisted_event = await db.get(GameEvent, event_id)
            persisted_empty = await db.get(GameEvent, no_period_id)
            persisted_periods = list(await db.scalars(select(GameEventOccurrence).where(
                GameEventOccurrence.event_id == event_id
            )))
            assert persisted_event.source_data == {"client": current, "notice_id": 42}
            assert persisted_empty.source_data == {"client": current}
            assert len(persisted_periods) == 2
            assert all(row.source_data == {"client": current, "schedule_source": "manual"}
                       for row in persisted_periods)
    finally:
        await engine.dispose()
