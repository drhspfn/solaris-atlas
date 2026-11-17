"""Source-query counts and exact presentation parity on isolated PostgreSQL."""

import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event as sql_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from test_admin_console import console_database as console_database
from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
from wuwa_story.db.models.notices import GameNotice, GameNoticeRevision
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference, FileType
from wuwa_story.db.repositories.event_browsing import SqlAlchemyEventRepository
from wuwa_story.events.domain import CLIENT_SOURCE
from wuwa_story.events.repository import EVENT_RUN_BATCH_SIZE
from wuwa_story.events.service import EventService
from wuwa_story.read_models import source_sections
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.source_sections import _request_caches

pytestmark = pytest.mark.skipif(not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Isolated PostgreSQL required")


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "3.8.0"])
async def test_51_periods_share_one_header_and_keep_exact_source_presentation(console_database, version, monkeypatch):
    engine = create_async_engine(console_database, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    old = {"download_id": "old-" + suffix, "asset_version": "3.8.0",
           "artwork": [{"path": "/Game/old", "role": "background"}],
           "resources": [{"path": "/old-root"}],
           "locales": {"en": {"title": "Old title"}, "ja": {"title": "古い"}},
           "large_capture": "x" * 32768}
    current = {**old, "download_id": "new-" + suffix, "asset_version": "3.9.0",
               "artwork": [{"path": "/Game/current", "role": "background"}]}
    try:
        async with factory() as db:
            root = GameEvent(source=CLIENT_SOURCE, source_id="batch-root:" + suffix, title="Root",
                             event_kind="limited", source_url="https://official", source_revision=suffix,
                             source_data={"client": current})
            db.add(root)
            await db.flush()
            entity = GameEvent(source=CLIENT_SOURCE, source_id="activity:" + suffix, title="Event",
                               canonical_event_id=root.id, event_kind="limited", source_url="https://official",
                               banner_path="UI/banner.png", source_revision=suffix,
                               source_data={"client": current, "large_header": "x" * 65536})
            db.add(entity)
            await db.flush()
            identity, canonical = entity.id, root.id
            notice = GameNotice(feed="test", locale="en", source_id=suffix, category="event",
                                title="Notice", source_url="https://official", revision="current",
                                related_event_ids=[identity], content={"banner_url": "https://new-notice"})
            db.add(notice)
            await db.flush()
            db.add(GameNoticeRevision(notice_id=notice.id, revision="old", content={"banner_url": "https://old-notice"}))
            if await db.get(GameEventSnapshot, "3.8.0") is None:
                db.add(GameEventSnapshot(version="3.8.0", download_id="test", complete=True))
                await db.flush()
            db.add(GameEventSnapshotMember(version="3.8.0", event_id=identity, client_capture=old))
            for server in ("asia", "europe", "america"):
                for index in range(17):
                    client = old if index % 2 else current
                    # Same-capture discoveries must survive versioned enrichment.
                    client = {**client, "prefab_artwork": [{"path": "/Game/discovered", "role": "content"}]}
                    db.add(GameEventOccurrence(
                        event_id=identity, server=server, source_occurrence_id=f"manual:{index}",
                        game_version=None if index % 3 == 0 else "3.7.0" if index % 3 == 1 else "3.9.0",
                        starts_at=None if index % 3 == 0 else datetime(1900 + index, 1, 1, tzinfo=UTC),
                        ends_at=datetime(2027, 1, 1, tzinfo=UTC), source_data={"client": client,
                            "notice_id": notice.id, "notice_revision": "old", "schedule_source": "manual"}))
            file_type = await db.scalar(select(FileType.id).where(FileType.key == "image"))
            for path, download in (("UI/banner.png", current["download_id"]),
                                   ("archive/UI/banner.png", old["download_id"]),
                                   ("/Game/old", old["download_id"]),
                                   ("/Game/current", current["download_id"])):
                file = FileObject(file_type_id=file_type, mime_type="image/jpeg")
                db.add(file)
                await db.flush()
                db.add_all([FileLocation(file_id=file.id, backend="s3", bucket="test", is_primary=True,
                                         object_key=f"{suffix}/{file.id}", available=True),
                            FileReference(file_id=file.id, reference_type="event_artwork", source_path=path,
                                          metadata_json={"download_id": download})])
            await db.commit()
            ids = list(await db.execute(select(GameEventOccurrence.server, GameEventOccurrence.id)
                                         .where(GameEventOccurrence.event_id == identity)
                                         .order_by(GameEventOccurrence.id)))

        queries = []
        def record(_, __, statement, ___, context, ____):
            if statement.lstrip().startswith("SELECT"):
                columns = getattr(context.compiled.statement, "selected_columns", [])
                queries.append(any(column.shares_lineage(GameEvent.id) for column in columns))
        sql_event.listen(engine.sync_engine, "before_cursor_execute", record)
        measurements = []
        results = []
        for batched in (False, True):
            queries.clear()
            started = time.perf_counter()
            rendered = {}
            async with factory() as db:
                service = EventService(SqlAlchemyEventRepository(db, "test"), lambda key: key, lambda url: url)
                with source_build(), _request_caches(db):
                    if batched:
                        for server in ("asia", "europe", "america"):
                            group = [period for region, period in ids if region == server]
                            for offset in range(0, len(group), EVENT_RUN_BATCH_SIZE):
                                rows = await service.browse(server, version=version, locale="ja", event_id=identity,
                                    occurrence_ids=group[offset:offset + EVENT_RUN_BATCH_SIZE])
                                rendered.update({row["occurrence_id"]: row for row in rows["events"]})
                    else:
                        for server, period in ids:
                            rows = await service.browse(server, version=version, locale="ja", event_id=identity,
                                                        occurrence_id=period)
                            rendered[period] = rows["events"][0]
                    undated = (await service.browse("", version=version, locale="ja", event_id=identity))["events"]
                assert db.info == {}
            measurements.append((len(queries), sum(queries), time.perf_counter() - started))
            results.append((rendered, undated))
        assert results[0] == results[1]
        assert len(results[1][0]) == 51
        assert all(row["canonical_event_id"] == canonical for row in results[1][0].values())
        assert any(row["starts_at"] is None for row in results[1][0].values())
        assert any(row["starts_at"] and row["starts_at"].year == 1901 for row in results[1][0].values())
        assert measurements[0][1] == 52 and measurements[1][1] == 1
        assert measurements[1][0] < measurements[0][0] / 3
        print(f"event batch version={version or 'current'}: legacy queries/headers/seconds={measurements[0]}; batch={measurements[1]}")
        # Exercise the actual builder after the independent-period semantic patch:
        # known periods from other versions and unversioned manual dates all survive.
        payloads = []
        for batched in (False, True):
            async def browse(server, kind, selected, locale, db, _batched=batched, **kwargs):
                service = EventService(SqlAlchemyEventRepository(db, "test"), lambda key: key, lambda url: url)
                if not _batched and (identities := kwargs.pop("occurrence_ids", None)) is not None:
                    rows = []
                    for period in identities:
                        rows.extend((await service.browse(server, version=selected, locale=locale,
                            **kwargs, occurrence_id=period))["events"])
                    return {"events": rows}
                return await service.browse(server, kind, selected, locale, **kwargs)
            monkeypatch.setattr(source_sections, "browse_events", browse)
            async with factory() as db:
                with source_build(), _request_caches(db):
                    payloads.append(await source_sections._event_payload(db, identity, "ja", version or "current"))
        assert payloads[0] == payloads[1]
        assert len(payloads[1]["occurrences"]) + len(payloads[1]["unversioned_occurrences"]) == 51
        assert {row["game_version"] for row in payloads[1]["occurrences"]} >= {"3.7.0", "3.9.0"}
    finally:
        await engine.dispose()
