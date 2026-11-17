"""Notice revisions and independent periods against a disposable PostgreSQL database."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from test_admin_console import console_database as console_database
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.ingestion.game_events import CLIENT_SOURCE
from wuwa_story.ingestion.notices import store_notices

pytestmark = pytest.mark.skipif(not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Isolated PostgreSQL required")


def notice(identity, duration):
    return {"id": identity, "tabTitle": "[Artisan's Search] Featured Combat Event", "whiteList": [],
            "content": f"<p>✦Duration✦</p><p>{duration}</p><p>✦Rules✦</p>"}


@pytest.mark.asyncio
async def test_notice_revision_preserves_manual_and_distinct_same_start_periods(console_database):
    engine = create_async_engine(console_database, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        event = GameEvent(source=CLIENT_SOURCE, source_id="activity:identity-test", title="Artisan's Search",
                          event_kind="limited", source_url="https://example.test", source_revision="test",
                          source_data={"client": {"download_id": "test"}})
        db.add(event)
        await db.flush()
        event_id = event.id
        manual = GameEventOccurrence(event_id=event_id, source_occurrence_id="manual:independent", server="asia",
            starts_at=datetime(2026, 11, 19, 20, tzinfo=UTC), ends_at=datetime(2027, 1, 1, tzinfo=UTC),
            source_data={"schedule_source": "manual"})
        db.add(manual)
        await db.commit()

    unknown = notice("90001", "Version 3.8 Update - 2026-12-21 03:59 (server time)")
    confirmed = notice("90001", "2026-11-20 04:00 - 2026-12-21 03:59 (server time)")
    second = notice("90002", "2026-11-20 04:00 - 2026-12-21 03:59 (server time)")
    async with factory() as db:
        for raw in (unknown, confirmed, confirmed, second):
            await store_notices(db, "https://feed.test", "https://board.test", {"game": [raw]})
            await db.commit()
        rows = list(await db.scalars(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == event_id).order_by(GameEventOccurrence.id)))
        assert len(rows) == 7
        assert sum(row.source_data.get("schedule_source") == "manual" for row in rows) == 1
        for server in ("asia", "europe", "america"):
            pair = [row for row in rows if row.server == server and row.source_data.get("notice_id")]
            assert len(pair) == 2 and pair[0].starts_at == pair[1].starts_at
            assert pair[0].source_data["notice_id"] != pair[1].source_data["notice_id"]
        saved = next(row for row in rows if row.source_occurrence_id == "manual:independent")
        assert saved.ends_at == datetime(2027, 1, 1, tzinfo=UTC)
        assert saved.source_data == {"schedule_source": "manual"}
    await engine.dispose()
