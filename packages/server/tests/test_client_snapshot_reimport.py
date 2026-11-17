"""Exercise snapshot preservation with real transactions on an isolated database."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from wuwa_story.db.models.game_events import (
    GameEvent,
    GameEventOccurrence,
    GameEventSnapshot,
    GameEventSnapshotMember,
)
from wuwa_story.events.canonical import bind_event_alias, event_family_ids
from wuwa_story.ingestion.client_events import store_client_events
from wuwa_story.ingestion.event_capture_backfill import backfill_event_captures


class LocalSession(AsyncSession):
    async def execute(self, statement, *args, **kwargs):
        if str(statement) in {"SELECT pg_advisory_xact_lock(705, 1)", "SELECT pg_advisory_xact_lock(705, 25)"}:
            return None  # SQLite has no advisory locks; production uses PostgreSQL.
        return await super().execute(statement, *args, **kwargs)


@pytest_asyncio.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.execute(text("ATTACH DATABASE ':memory:' AS core"))
        await connection.execute(text("""CREATE TABLE core.game_event (
            id INTEGER PRIMARY KEY, canonical_event_id INTEGER REFERENCES game_event(id), canonical_evidence JSON,
            source TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
            slug TEXT, description TEXT, event_kind TEXT NOT NULL, group_label_key TEXT, game_path TEXT,
            banner_path TEXT, rewards JSON, source_url TEXT NOT NULL, source_revision TEXT NOT NULL,
            source_data JSON NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"""))
        await connection.execute(text("""CREATE TABLE core.game_event_occurrence (
            id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, source_occurrence_id TEXT NOT NULL,
            game_version TEXT, server TEXT NOT NULL, starts_at DATETIME, ends_at DATETIME,
            banner_path TEXT, rewards JSON, season JSON, source_data JSON NOT NULL)"""))
        await connection.execute(text("""CREATE TABLE core.game_event_snapshot (
            version TEXT PRIMARY KEY, download_id TEXT NOT NULL, complete BOOLEAN NOT NULL)"""))
        await connection.execute(text("""CREATE TABLE core.game_event_snapshot_member (
            version TEXT NOT NULL REFERENCES game_event_snapshot(version) ON DELETE CASCADE,
            event_id INTEGER NOT NULL, client_capture JSON, PRIMARY KEY(version,event_id))"""))
        await connection.execute(text("""INSERT INTO core.game_event
            (id, source, source_id, title, event_kind, source_url, source_revision, source_data)
            VALUES (1,'wuwa-game-client','activity:100','First','limited','https://example.test','old','{}'),
                   (2,'wuwa-game-client','activity:101','Second','limited','https://example.test','old','{}')"""))
        await connection.execute(text("""INSERT INTO core.game_event_occurrence
            (event_id,source_occurrence_id,game_version,server,source_data)
            VALUES (1,'existing','3.8','asia','{}'), (1,'existing','3.8','europe','{}'),
                   (1,'existing','3.8','america','{}'), (2,'existing','3.8','asia','{}'),
                   (2,'existing','3.8','europe','{}'), (2,'existing','3.8','america','{}')"""))
        await connection.execute(text("INSERT INTO core.game_event_snapshot VALUES ('3.8.0','pinned',1)"))
        await connection.execute(text("INSERT INTO core.game_event_snapshot_member (version,event_id) VALUES ('3.8.0',1),('3.8.0',2)"))
    yield async_sessionmaker(engine, class_=LocalSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


def record(title):
    return {"event_kind": "limited", "artwork": [], "resources": [],
            "locales": {"en": {"title": title}}}


async def snapshot(session):
    head = await session.get(GameEventSnapshot, "3.8.0")
    members = set(await session.scalars(select(GameEventSnapshotMember.event_id)))
    return head.download_id, head.complete, members


@pytest.mark.asyncio
async def test_canonical_binding_retains_original_sources_members_and_periods(database):
    evidence = {"basis": "game_source", "reference": "release:22/pool:100096"}
    async with database() as session:
        await bind_event_alias(session, 2, 1, evidence)
        await bind_event_alias(session, 2, 1, evidence)
        assert await event_family_ids(session, 2) == (1, [1, 2])
        assert (await session.get(GameEvent, 2)).source_id == "activity:101"
        assert len(list(await session.scalars(select(GameEventOccurrence)))) == 6
        assert await snapshot(session) == ("pinned", True, {1, 2})
        with pytest.raises(ValueError, match="root"):
            await bind_event_alias(session, 1, 2, evidence)
        with pytest.raises(ValueError, match="changed"):
            await bind_event_alias(session, 2, 1, {"basis": "manual", "reference": "review"})
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicting_history", [False, True])
async def test_import_binds_only_proven_ordinary_banner_variants(database, conflicting_history):
    def banner(role, kind):
        return {**record("Banner"), "table": "GachaViewInfo", "gacha_type": kind,
                "featured_ids": [role], "event_kind": "banner"}

    async with database() as session:
        first, second = await session.get(GameEvent, 1), await session.get(GameEvent, 2)
        first.source_id, second.source_id = "gacha:739", "gacha:1893"
        head = await session.get(GameEventSnapshot, "3.8.0")
        head.complete = False
        if conflicting_history:
            session.add(GameEventSnapshot(version="3.7.0", download_id="older", complete=True))
            await session.flush()
            session.add(GameEventSnapshotMember(version="3.7.0", event_id=2,
                                               client_capture=banner(999, 2)))
        await session.flush()
        records = {"gacha:739": banner(1210, 2), "gacha:1893": banner(1210, 2),
                   "gacha:1918": banner(1210, 9)}
        await store_client_events(session, records, "3.8.0", "pinned")
        await store_client_events(session, records, "3.8.0", "pinned")
        assert first.canonical_event_id is None
        assert second.canonical_event_id == (None if conflicting_history else first.id)
        option = await session.scalar(select(GameEvent).where(GameEvent.source_id == "gacha:1918"))
        assert option.canonical_event_id is None
        assert len(list(await session.scalars(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id.in_([1, 2]))))) == 6
        assert {member.event_id for member in await session.scalars(select(GameEventSnapshotMember).where(
            GameEventSnapshotMember.version == "3.8.0"))} == {1, 2, option.id}
        if not conflicting_history:
            for changed_id in ("gacha:739", "gacha:1893"):
                with pytest.raises(ValueError, match="canonical banner binding"):
                    await store_client_events(session, {changed_id: banner(999, 2)}, "3.9.0", "future")


@pytest.mark.asyncio
async def test_partial_reimport_preserves_complete_snapshot_and_members(database):
    async with database() as session:
        result = await store_client_events(session, {"100": record("Updated"), "101": {"locales": {}}},
                                           "3.8.0", "pinned")
        await session.commit()
        assert result["unresolved"]
        assert await snapshot(session) == ("pinned", True, {1, 2})


@pytest.mark.asyncio
async def test_partial_reimport_cannot_add_members_to_complete_snapshot(database):
    async with database() as session:
        with pytest.raises(ValueError, match="differs"):
            await store_client_events(session, {"100": record("First"), "999": record("Unexpected"),
                                                "101": {"locales": {}}}, "3.8.0", "pinned")
        assert await snapshot(session) == ("pinned", True, {1, 2})


@pytest.mark.asyncio
async def test_reimport_rejects_new_download_before_changing_events(database):
    async with database() as session:
        with pytest.raises(ValueError, match="different download"):
            await store_client_events(session, {"100": record("Wrong")}, "3.8.0", "other")
        assert await session.scalar(text("SELECT title FROM core.game_event WHERE id=1")) == "First"
        assert await snapshot(session) == ("pinned", True, {1, 2})


@pytest.mark.asyncio
async def test_complete_reimport_requires_exact_membership(database):
    async with database() as session:
        with pytest.raises(ValueError, match="differs"):
            await store_client_events(session, {"100": record("Wrong")}, "3.8.0", "pinned")
        assert await session.scalar(text("SELECT title FROM core.game_event WHERE id=1")) == "First"
        assert await snapshot(session) == ("pinned", True, {1, 2})
        await store_client_events(session, {"100": record("First updated"), "101": record("Second updated")},
                                  "3.8.0", "pinned")
        await session.commit()
        assert await snapshot(session) == ("pinned", True, {1, 2})
        assert await session.scalar(text("SELECT title FROM core.game_event WHERE id=1")) == "First updated"


@pytest.mark.asyncio
async def test_incomplete_snapshot_can_be_completed_only_by_same_download(database):
    async with database() as session:
        await session.execute(text("UPDATE core.game_event_snapshot SET complete=0 WHERE version='3.8.0'"))
        await session.execute(text("DELETE FROM core.game_event_snapshot_member WHERE event_id=2"))
        first = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        first.client_capture = {**record("First"), "asset_version": "3.8.0", "download_id": "pinned",
                                "schedule_status": "server_controlled"}
        await session.commit()
        with pytest.raises(ValueError, match="different download"):
            await store_client_events(session, {"100": record("Wrong")}, "3.8.0", "other")
        assert await snapshot(session) == ("pinned", False, {1})
        await store_client_events(session, {"100": record("First"), "101": record("Second")},
                                  "3.8.0", "pinned")
        await session.commit()
        # Reload after the bulk delete/insert so ORM identity-map state cannot
        # mask the value persisted by the transaction.
        session.expire_all()
        assert await snapshot(session) == ("pinned", True, {1, 2})
        assert (await session.get(GameEventSnapshotMember, ("3.8.0", 1))).client_capture["locales"]["en"]["title"] == "First"
        assert (await session.get(GameEventSnapshotMember, ("3.8.0", 2))).client_capture["download_id"] == "pinned"


@pytest.mark.asyncio
async def test_versioned_capture_is_idempotent_and_conflicting_payload_fails_closed(database):
    async with database() as session:
        records = {"100": record("First"), "101": record("Second")}
        await store_client_events(session, records, "3.8.0", "pinned")
        await session.commit()
        first = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        assert first.client_capture["asset_version"] == "3.8.0"
        assert first.client_capture["download_id"] == "pinned"
        original = first.client_capture.copy()
        event = await session.get(GameEvent, 1)
        event.source_data = {**event.source_data, "client": {**event.source_data["client"],
                             "prefab_artwork": [{"path": "/Game/Enriched"}]}}
        await session.commit()
        await store_client_events(session, records, "3.8.0", "pinned")
        await session.commit()
        assert first.client_capture == original
        assert event.source_data["client"]["prefab_artwork"] == [{"path": "/Game/Enriched"}]
        with pytest.raises(ValueError, match="capture differs"):
            await store_client_events(session, {"100": record("Different"), "101": record("Second")},
                                      "3.8.0", "pinned")
        assert first.client_capture == original
        assert (await session.get(GameEvent, 1)).title == "First"


@pytest.mark.asyncio
async def test_pool_proof_recompile_requires_checksum_and_preserves_capture_identity(database):
    old = {**record("First"), "source_checksum": "a" * 64, "compiler_checksum": "b" * 64}
    proof = {"gacha_id": 100096, "view_id": 31, "members": [31, 42],
             "pool_checksum": "c" * 64, "gacha_checksum": "d" * 64, "group_checksum": "e" * 64}
    async with database() as session:
        await store_client_events(session, {"100": old, "101": record("Second")}, "3.8.0", "pinned")
        await session.commit()
        member = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        with pytest.raises(ValueError, match="compiler provenance"):
            await store_client_events(session, {"100": {**old, "compiler_checksum": None,
                                      "gacha_pool_proof": proof}, "101": record("Second")}, "3.8.0", "pinned")
        await store_client_events(session, {"100": {**old, "compiler_checksum": "f" * 64,
                                  "gacha_pool_proof": proof}, "101": record("Second")}, "3.8.0", "pinned")
        assert member.client_capture["gacha_pool_proof"] == proof
        assert member.client_capture["source_checksum"] == "a" * 64


@pytest.mark.asyncio
async def test_recompile_corrects_derived_resources_without_rewriting_period(database):
    old = {**record("First"), "source_checksum": "a" * 64, "compiler_checksum": "b" * 64,
           "resources": [{"path": "/Game/ShipTower"}], "artwork": [{"path": "/Game/ShipTower"}],
           "activity_module": "NewTowerClimbing"}
    corrected = {**old, "compiler_checksum": "c" * 64, "resources": [], "artwork": []}
    second = record("Second")
    async with database() as session:
        await store_client_events(session, {"100": old, "101": second}, "3.8.0", "pinned")
        period = await session.scalar(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == 1, GameEventOccurrence.server == "europe"))
        period.starts_at = datetime(2026, 1, 1, tzinfo=UTC)
        saved_period = dict(period.source_data)
        event = await session.get(GameEvent, 1)
        event.source_data = {"client": {**event.source_data["client"], "prefab_artwork": ["stale"]}}
        member = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        member.client_capture = {**member.client_capture, "prefab_artwork": ["stale"]}
        await session.flush()
        await store_client_events(session, {"100": corrected, "101": second}, "3.8.0", "pinned")
        capture = (await session.get(GameEventSnapshotMember, ("3.8.0", 1))).client_capture
        assert capture["resources"] == [] and capture["artwork"] == []
        assert capture["compiler_checksum"] == "c" * 64
        assert capture["source_checksum"] == "a" * 64
        assert period.source_data == saved_period
        assert "prefab_artwork" not in event.source_data["client"]
        assert "prefab_artwork" not in capture
        await store_client_events(session, {"100": corrected, "101": second}, "3.8.0", "pinned")
        with pytest.raises(ValueError, match="capture differs"):
            await store_client_events(session, {"100": {**corrected, "source_checksum": "d" * 64},
                                                "101": second}, "3.8.0", "pinned")
        with pytest.raises(ValueError, match="compiler provenance"):
            await store_client_events(session, {"100": {**corrected, "resources": ["unexpected"],
                                                        "compiler_checksum": None}, "101": second},
                                      "3.8.0", "pinned")


@pytest.mark.asyncio
async def test_older_import_retains_newer_current_and_separate_captures(database):
    async with database() as session:
        await store_client_events(session, {"100": record("New"), "101": record("New 2")},
                                  "3.8.0", "pinned")
        await session.commit()
        event = await session.get(GameEvent, 1)
        event.source_data = {**event.source_data, "client": {**event.source_data["client"],
                             "prefab_artwork": [{"path": "/Game/NewOnly"}]}}
        await session.commit()
        await store_client_events(session, {"100": record("Old"), "101": record("Old 2")},
                                  "3.7.0", "older-download")
        await session.commit()
        assert (await session.get(GameEvent, 1)).title == "New"
        old = await session.get(GameEventSnapshotMember, ("3.7.0", 1))
        new = await session.get(GameEventSnapshotMember, ("3.8.0", 1))
        assert old.client_capture["locales"]["en"]["title"] == "Old"
        assert old.client_capture["download_id"] == "older-download"
        assert "prefab_artwork" not in old.client_capture
        assert new.client_capture["locales"]["en"]["title"] == "New"
        assert new.client_capture["download_id"] == "pinned"


@pytest.mark.asyncio
async def test_lily_catalog_capture_preserves_one_existing_notice_period_and_art(database):
    async with database() as session:
        event = await session.get(GameEvent, 1)
        event.source_id = "gacha:100090"
        lily = {**record("Lily · Resonator Convene"), "event_kind": "banner",
                "table": "GachaViewInfo", "featured_ids": [1512], "up_ids": [1102, 1103, 1202],
                "banner_path": "/Game/Lily", "artwork": [{"path": "/Game/Lily", "role": "background"}]}
        recorded_capture = {**lily, "asset_version": "3.8.0", "download_id": "pinned",
                            "schedule_status": "server_controlled"}
        period = await session.scalar(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == 1, GameEventOccurrence.server == "europe"))
        period.source_occurrence_id = "notice:50623@update"
        period.game_version = None  # The notice states the update, not a recorded schedule version.
        period.starts_at = datetime(2026, 11, 11, tzinfo=UTC)
        period.ends_at = datetime(2026, 12, 3, tzinfo=UTC)
        period.banner_path = "/Game/Lily"
        period.source_data = {"client": recorded_capture, "notice_id": 36, "notice_revision": "notice-rev"}
        await session.commit()
        for _ in range(2):
            await store_client_events(session, {"gacha:100090": lily, "101": record("Second")},
                                      "3.8.0", "pinned")
            await session.commit()
        capture = (await session.get(GameEventSnapshotMember, ("3.8.0", 1))).client_capture
        rows = list(await session.scalars(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == 1, GameEventOccurrence.server == "europe")))
        assert len(rows) == 1 and rows[0].id == period.id
        assert rows[0].source_occurrence_id == "notice:50623@update"
        assert rows[0].source_data["notice_id"] == 36 and rows[0].banner_path == "/Game/Lily"
        assert capture["download_id"] == rows[0].source_data["client"]["download_id"] == "pinned"
        assert capture["artwork"] == rows[0].source_data["client"]["artwork"]


@pytest.mark.asyncio
async def test_end_only_period_capture_is_not_overwritten(database):
    async with database() as session:
        period = await session.scalar(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == 1, GameEventOccurrence.server == "europe"))
        period.ends_at = datetime(2026, 12, 3, tzinfo=UTC)
        period.source_data = {"client": {"download_id": "old", "locales": {"en": {"title": "Old"}}}}
        period.banner_path = "/Game/Old"
        await session.commit()
        await store_client_events(session, {"100": record("New"), "101": record("Second")}, "3.8.0", "pinned")
        await session.commit()
        assert period.source_data["client"]["download_id"] == "old" and period.banner_path == "/Game/Old"
        rows = list(await session.scalars(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == 1, GameEventOccurrence.server == "europe")))
        assert len(rows) == 2  # A conflicting capture is not treated as the same period.


@pytest.mark.asyncio
async def test_import_preserves_manual_period_with_unknown_dates(database):
    async with database() as session:
        period = await session.scalar(select(GameEventOccurrence).where(
            GameEventOccurrence.event_id == 1, GameEventOccurrence.server == "europe"))
        period.source_occurrence_id = "manual:reviewed"
        period.source_data = {"schedule_source": "manual"}
        period.banner_path = "/Game/Reviewed"
        await session.flush()
        await store_client_events(session, {"100": record("First"), "101": record("Second")},
                                  "3.8.0", "pinned")
        assert period.source_data == {"schedule_source": "manual"}
        assert period.banner_path == "/Game/Reviewed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,subject", [(2, 1210), (3, 21010016)])
async def test_new_patch_without_old_view_keeps_existing_event_identity(database, kind, subject):
    banner = {**record("Ordinary banner"), "table": "GachaViewInfo", "gacha_type": kind,
              "featured_ids": [subject], "event_kind": "banner"}
    async with database() as session:
        head = await session.get(GameEventSnapshot, "3.8.0")
        head.complete = False
        (await session.get(GameEvent, 1)).source_id = "gacha:739"
        await session.flush()
        await store_client_events(session, {"gacha:739": banner, "101": record("Second")}, "3.8.0", "pinned")
        await session.commit()
        original = await session.scalar(select(GameEvent).where(GameEvent.source_id == "gacha:739"))
        await store_client_events(session, {"gacha:1893": banner}, "3.9.0", "next-download")
        await session.commit()
        added = await session.scalar(select(GameEvent).where(GameEvent.source_id == "gacha:1893"))
        assert added.canonical_event_id == original.id
        assert added.canonical_evidence["basis"] == "catalog_identity_policy"
        assert (await session.get(GameEventSnapshotMember, ("3.8.0", original.id))).client_capture
        assert await session.get(GameEventSnapshotMember, ("3.9.0", original.id)) is None


async def retained_capture(session):
    capture = {**record("First"), "table": "Activity", "asset_version": "3.8.0", "download_id": "pinned",
               "schedule_status": "server_controlled", "title_key": "Activity_100_Title"}
    event = await session.get(GameEvent, 1)
    event.source_data = {"client": capture}
    occurrences = list(await session.scalars(select(GameEventOccurrence).where(GameEventOccurrence.event_id == 1)))
    for row in occurrences:
        row.source_data = {"client": dict(capture)}
    await session.commit()
    return capture, occurrences


@pytest.mark.asyncio
async def test_capture_backfill_dry_run_exact_proof_and_idempotence(database):
    async with database() as session:
        capture, occurrences = await retained_capture(session)
        preview = await backfill_event_captures(session, "3.8.0", "pinned")
        assert preview["ready"] == [1] and preview["applied"] == 0
        assert (await session.get(GameEventSnapshotMember, ("3.8.0", 1))).client_capture is None
        result = await backfill_event_captures(session, "3.8.0", "pinned", apply=True)
        await session.commit()
        assert result["applied"] == 1
        saved = (await session.get(GameEventSnapshotMember, ("3.8.0", 1))).client_capture
        assert {key: value for key, value in saved.items() if key != "_capture_provenance"} == capture
        assert saved["_capture_provenance"] == {"basis": "consistent_retained_captures",
                                               "source_id": "activity:100",
                                               "occurrence_ids": sorted(row.id for row in occurrences)}
        assert (await backfill_event_captures(session, "3.8.0", "pinned", apply=True))["applied"] == 0
        assert (await backfill_event_captures(session, "3.8.0", "pinned", after_event_id=1))["last_event_id"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["different_payload", "different_download", "shared_key"])
async def test_capture_backfill_skips_ambiguous_retained_data(database, conflict):
    async with database() as session:
        capture, occurrences = await retained_capture(session)
        if conflict == "shared_key":
            capture["title_key"] = "Activity_999_Title"
            (await session.get(GameEvent, 1)).source_data = {"client": dict(capture)}
            for row in occurrences:
                row.source_data = {"client": dict(capture)}
        else:
            changed = dict(capture)
            if conflict == "different_payload":
                changed["title_key"] = "Activity_101_Title"
            else:
                changed["download_id"] = "other"
            occurrences[0].source_data = {"client": changed}
        await session.commit()
        result = await backfill_event_captures(session, "3.8.0", "pinned", apply=True)
        assert 1 not in result["ready"]
        assert any(row == {"event_id": 1, "reason": "conflicting_or_shared_evidence"} for row in result["skipped"])
        assert (await session.get(GameEventSnapshotMember, ("3.8.0", 1))).client_capture is None
        with pytest.raises(ValueError, match="exact version and download"):
            await backfill_event_captures(session, "3.8.0", "other")
