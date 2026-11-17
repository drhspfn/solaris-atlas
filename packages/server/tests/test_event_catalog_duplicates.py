import pytest

from wuwa_story.events.catalog_duplicates import visible_occurrences
from wuwa_story.read_models.readers import events as reader


def test_only_identical_event_placeholders_are_suppressed():
    rows = [(716, 1998, "3.7"), (716, 6210, "3.8.0"),
            (739, 2067, "3.5"), (739, 4161, "3.7.1"), (739, 6063, "3.8.0"),
            (1893, 4026, "3.7.1"), (1893, 5928, "3.8.0"),
            (1850, 1, "3.9"), (1850, 2, "3.10"),
            (1918, 3, "3.8"), (716, 4, "3.9")]
    # A different undated non-placeholder may be a real run and remains visible.
    assert visible_occurrences(rows, {6210, 4161, 6063, 4026, 5928, 1, 2, 3}) == {5928, 2, 3}


@pytest.mark.asyncio
async def test_projection_all_versions_filters_only_proven_placeholders(monkeypatch):
    from types import SimpleNamespace

    base = {"id": 716, "title": "Suoming", "kind": "banner", "artwork": []}
    dated = {**base, "server": "europe", "occurrence_id": 1, "game_version": "3.7",
             "catalog_version": "3.7.1", "starts_at": "2026-10-22T09:00:00+00:00"}
    placeholder = {**base, "server": "europe", "occurrence_id": 2, "game_version": "3.8",
                   "catalog_version": "3.8.0", "starts_at": None, "_catalog_placeholder": True}
    entry = SimpleNamespace(entity_id=716, payload={"event": base, "occurrences": [dated, placeholder]})

    async def entries(*args, **kwargs):
        return [entry]

    monkeypatch.setattr(reader.entities, "_scope_entries", entries)
    all_events = (await reader.events_page(None, "europe"))["events"]
    assert [row["occurrence_id"] for row in all_events] == [1]
    version_events = (await reader.events_page(None, "europe", version="3.8"))["events"]
    assert [row["occurrence_id"] for row in version_events] == [2]
    assert "_catalog_placeholder" not in version_events[0]


@pytest.mark.asyncio
async def test_source_calendar_filters_only_unfiltered_list():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from wuwa_story.db.repositories.event_browsing import read_event_runs

    event = SimpleNamespace(id=716)
    dated = SimpleNamespace(id=1, source_occurrence_id="100087", starts_at="date",
                            ends_at="date", game_version="3.7")
    stub = SimpleNamespace(id=2, source_occurrence_id="client:3.8.0", starts_at=None,
                           ends_at=None, game_version="3.8")
    session = AsyncMock()
    session.execute.return_value = Mock(all=Mock(return_value=[(event, dated), (event, stub)]))
    assert await read_event_runs(session, server="europe") == [(event, dated)]
    assert await read_event_runs(session, server="europe", version="3.8") == [
        (event, dated), (event, stub)
    ]  # SQL applies the version predicate in production.
