"""Source-build media reuse retains all captures, negatives and request isolation."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine

from wuwa_story.db.repositories.event_browsing import read_event_media
from wuwa_story.read_models.context import source_build
from wuwa_story.read_models.source_sections import _request_caches


@pytest.mark.asyncio
async def test_repeated_runs_recompute_capture_fallback_and_keep_complete_rows():
    exact = ("UI/banner.png", "current", {"download_id": "current", "extra": {"slot": 2}}, 1)
    historical = ("capture/UI/banner.png", "old", {"download_id": "old", "artwork": [1, 2]}, 2)
    other = ("capture2/UI/banner.png", "other", {"download_id": "other"}, 3)
    session = Row(info={}, execute=AsyncMock(side_effect=[[exact], [historical, other]]))
    with source_build(), _request_caches(session):
        assert await read_event_media(
            session, {"UI/banner.png"}, "public", captures={("current", "UI/banner.png")}
        ) == [exact[:3]]
        # Cached exact rows from one capture do not resolve a different capture.
        assert await read_event_media(
            session, {"UI/banner.png"}, "public", captures={("old", "UI/banner.png")}
        ) == [exact[:3], historical[:3], other[:3]]
        # Suffix cache stores ALL captures, not only the capture that requested it.
        assert await read_event_media(
            session, {"UI/banner.png"}, "public", captures={("other", "UI/banner.png")}
        ) == [exact[:3], historical[:3], other[:3]]
        # A now-resolved exact capture must not acquire unnecessary suffix rows.
        assert await read_event_media(
            session, {"UI/banner.png"}, "public", captures={("current", "UI/banner.png")}
        ) == [exact[:3]]
        assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_negative_exact_and_suffix_results_are_cached_and_new_paths_still_query():
    session = Row(info={}, execute=AsyncMock(return_value=[]))
    with source_build(), _request_caches(session):
        for capture in ("old", "new", "future"):
            assert (
                await read_event_media(
                    session,
                    {"UI/missing.png", "/Game/Missing"},
                    "public",
                    captures={(capture, "UI/missing.png"), (capture, "/Game/Missing")},
                )
                == []
            )
        assert session.execute.await_count == 2
        assert await read_event_media(session, {"UI/missing.png", "UI/new.png"}, "public") == []
        assert session.execute.await_count == 4  # Only the newly encountered path.
        assert await read_event_media(session, set(), "public") == []
        assert session.execute.await_count == 4
        assert await read_event_media(session, {"UI/missing.png"}, "private") == []
        assert session.execute.await_count == 6  # Negatives are also bucket-scoped.
        assert await read_event_media(session, {"UI/missing.png"}, "public") == []
        assert session.execute.await_count == 6


@pytest.mark.asyncio
async def test_bucket_isolation_preserves_per_path_row_order_and_capture_metadata():
    first = ("/Game/Banner", "first", {"download_id": "a"}, 1)
    second = ("/Game/Banner", "second", {"download_id": "b"}, 2)
    private = ("/Game/Banner", "private", {"download_id": "a"}, 1)
    session = Row(info={}, execute=AsyncMock(side_effect=[[first, second], [private]]))
    with source_build(), _request_caches(session):
        assert await read_event_media(session, {"/Game/Banner"}, "public") == [
            first[:3],
            second[:3],
        ]
        assert await read_event_media(session, {"/Game/Banner"}, "private") == [private[:3]]
        assert await read_event_media(session, {"/Game/Banner"}, "public") == [
            first[:3],
            second[:3],
        ]
        assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_overlapping_suffix_caches_union_by_reference_without_dropping_distinct_refs():
    first = ("capture/UI/a.png", "image", {"download_id": "old"}, 1)
    duplicate_ref = (*first[:3], 2)  # Distinct source references remain distinct rows.
    additional = ("other/a.png", "other", {"download_id": "other"}, 3)
    session = Row(
        info={},
        execute=AsyncMock(
            side_effect=[[], [first, duplicate_ref], [], [additional, first, duplicate_ref]]
        ),
    )
    with source_build(), _request_caches(session):
        assert await read_event_media(session, {"UI/a.png"}, "public") == [
            first[:3],
            duplicate_ref[:3],
        ]
        expected = [additional[:3], first[:3], duplicate_ref[:3]]
        assert await read_event_media(session, {"UI/a.png", "a.png"}, "public") == expected
        assert await read_event_media(session, {"UI/a.png", "a.png"}, "public") == expected
        assert session.execute.await_count == 4


@pytest.mark.asyncio
async def test_request_cache_is_cleared_between_entity_locale_builds_and_public_never_uses_it():
    session = Row(info={"unrelated": 7}, execute=AsyncMock(return_value=[]))
    for _ in range(2):
        with source_build(), _request_caches(session):
            await read_event_media(session, {"UI/missing.png"}, "public")
            await read_event_media(session, {"UI/missing.png"}, "public")
        assert session.info == {"unrelated": 7}
    assert session.execute.await_count == 4
    sentinel = {
        "exact": {("public", "UI/missing.png"): []},
        "suffix": {("public", "UI/missing.png"): []},
    }
    session.info["story_event_media"] = sentinel
    await read_event_media(session, {"UI/missing.png"}, "public")
    await read_event_media(session, {"UI/missing.png"}, "public")
    assert session.execute.await_count == 8
    assert session.info["story_event_media"] is sentinel


@pytest.mark.asyncio
async def test_cached_query_executes_real_sql_and_retains_metadata_and_reference_identity():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS storage")
            for statement in (
                "CREATE TABLE storage.file_reference (id INTEGER PRIMARY KEY, file_id INTEGER, source_path TEXT, metadata JSON)",
                "CREATE TABLE storage.file_object (id INTEGER PRIMARY KEY, mime_type TEXT)",
                "CREATE TABLE storage.file_location (file_id INTEGER, object_key TEXT, available BOOLEAN, is_primary BOOLEAN, backend TEXT, bucket TEXT)",
                'INSERT INTO storage.file_reference VALUES (1,1,\'UI/a.png\',\'{"download_id":"current"}\'),(2,2,\'capture/UI/a.png\',\'{"download_id":"old","extra":[1,2]}\')',
                "INSERT INTO storage.file_object VALUES (1,'image/png'),(2,'image/webp')",
                "INSERT INTO storage.file_location VALUES (1,'new.png',1,1,'s3','public'),(2,'old.webp',1,1,'s3','public')",
            ):
                connection.exec_driver_sql(statement)
            session = Row(
                info={},
                execute=AsyncMock(side_effect=lambda query: connection.execute(query).all()),
            )
            with source_build(), _request_caches(session):
                rows = await read_event_media(
                    session, {"UI/a.png"}, "public", captures={("old", "UI/a.png")}
                )
                assert rows == [
                    ("UI/a.png", "new.png", {"download_id": "current"}),
                    ("capture/UI/a.png", "old.webp", {"download_id": "old", "extra": [1, 2]}),
                ]
                assert (
                    await read_event_media(
                        session, {"UI/a.png"}, "public", captures={("old", "UI/a.png")}
                    )
                    == rows
                )
                assert session.execute.await_count == 2
    finally:
        engine.dispose()
