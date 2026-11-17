from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.events.selectable_pools import group_selectable_pools
from wuwa_story.read_models.readers import events as reader


def choices(pool=100096, notice=42, prefix=100096, names=None):
    names = names or ["Camellya", "Roccia", "Phoebe", "Brant"]
    return [{"id": index, "source_id": f"gacha:{prefix}{index}", "occurrence_id": 6300 + index,
             "title": "Same title", "kind": "banner", "starts_at": "2026-11-11T10:59:59+00:00",
             "ends_at": "2026-12-21T10:59:00+00:00", "featured_names": [name], "artwork": [],
             "_pool_match": {"basis": "gacha_pool", "release_id": 22, "gacha_id": pool,
                             "notice_id": notice, "notice_revision": "a" * 64}}
            for index, name in enumerate(names, 1)]


def test_proven_pool_is_one_card_with_all_original_identities():
    starpath = choices()
    tideforge = choices(200096, 43, 200096, ["Red Spring", "Tragicomedy", "Luminous Hymn", "Unflickering Valor"])
    for row in tideforge:
        row["id"] += 10
        row["occurrence_id"] += 100
    grouped = group_selectable_pools([*starpath, *tideforge], "europe", "en")
    assert len(grouped) == 2
    assert grouped[0]["featured_names"] == ["Camellya", "Roccia", "Phoebe", "Brant"]
    assert [member["id"] for member in grouped[0]["selectable_members"]] == [1, 2, 3, 4]
    assert grouped[0]["selectable_members"][1]["occurrence_id"] == 6302
    assert grouped[1]["featured_names"] == ["Red Spring", "Tragicomedy", "Luminous Hymn", "Unflickering Valor"]


@pytest.mark.parametrize("change", ["no_proof", "different_revision", "different_date", "different_pool"])
def test_equal_titles_without_matching_proof_and_run_are_not_merged(change):
    left, right = choices()[:2]
    right = {**right, "_pool_match": dict(right["_pool_match"])}
    if change == "no_proof":
        right.pop("_pool_match")
    elif change == "different_revision":
        right["_pool_match"]["notice_revision"] = "b" * 64
    elif change == "different_pool":
        right["_pool_match"]["gacha_id"] = 123
    else:
        right["ends_at"] = "2027-01-01T00:00:00+00:00"
    assert len(group_selectable_pools([left, right], "europe", "en")) == 2


def test_separate_pool_runs_keep_separate_histories():
    first = choices()
    second = [{**row, "occurrence_id": row["occurrence_id"] + 100,
               "starts_at": "2027-01-01T00:00:00+00:00", "ends_at": "2027-02-01T00:00:00+00:00",
               "_pool_match": {**row["_pool_match"], "notice_revision": "b" * 64}}
              for row in first]
    groups = group_selectable_pools([*first, *second], "europe", "en")
    assert len(groups) == 2
    assert {row["occurrence_id"] for row in groups[0]["selectable_members"]} == {6301, 6302, 6303, 6304}
    assert {row["occurrence_id"] for row in groups[1]["selectable_members"]} == {6401, 6402, 6403, 6404}


@pytest.mark.asyncio
@pytest.mark.parametrize(("pool", "notice", "prefix", "names"), [
    (100096, 42, 100096, ["Camellya", "Roccia", "Phoebe", "Brant"]),
    (200096, 43, 200096, ["Red Spring", "Tragicomedy", "Luminous Hymn", "Unflickering Valor"]),
])
async def test_projected_calendar_and_each_detail_keep_members(monkeypatch, pool, notice, prefix, names):
    rows = choices(pool, notice, prefix, names)
    entries = [SimpleNamespace(entity_id=row["id"], payload={"event": row,
               "occurrences": [{**row, "server": "europe"}]}) for row in rows]
    monkeypatch.setattr(reader.entities, "_scope_entries", AsyncMock(return_value=entries))
    async def page(_, kind, identity, *args, **kwargs):
        if kind == "game_notice":
            return {"revision": "a" * 64, "related_event_ids": [1, 2, 3, 4]}
        return next(entry.payload for entry in entries if entry.entity_id == identity)
    page_mock = AsyncMock(side_effect=page)
    monkeypatch.setattr(reader.entities, "page_payload", page_mock)
    calendar = (await reader.events_page(None, "europe", summary=True))["events"]
    assert len(calendar) == 1
    assert len(calendar[0]["selectable_members"]) == 4
    assert "_pool_match" not in calendar[0]
    reader.entities._scope_entries.reset_mock()
    detail = await reader.event_detail(None, 2, "europe", 6302)
    reader.entities._scope_entries.assert_not_awaited()
    assert [call.args[2] for call in page_mock.await_args_list if call.args[1] == "game_event"] == [2, 1, 3, 4]
    assert detail["id"] == 2 and detail["occurrence_id"] == 6302
    assert detail["featured_names"] == names
    assert len(detail["selectable_members"]) == 4
    assert "_pool_match" not in detail
    session = SimpleNamespace(scalars=AsyncMock(return_value=[1, 2, 3, 4]))
    versioned = (await reader.events_page(session, "europe", version="3.8", summary=True))["events"]
    assert len(versioned) == 1 and len(versioned[0]["selectable_members"]) == 4


@pytest.mark.asyncio
async def test_source_calendar_and_nonrepresentative_detail_retain_member_identity():
    from datetime import UTC, datetime

    from wuwa_story.events.domain import Event, EventOccurrence, EventRun
    from wuwa_story.events.repository import EventRepository
    from wuwa_story.events.service import EventService

    runs = []
    for row in choices():
        client = {"download_id": "same", "locales": {"en": {
            "title": row["title"], "featured_names": row["featured_names"]}}}
        event = Event(row["id"], None, row["source_id"], row["title"], None, "banner",
                      None, [], None, "https://example.test", {"client": client})
        match = row["_pool_match"]
        occurrence = EventOccurrence(row["occurrence_id"], None,
            datetime(2026, 11, 11, tzinfo=UTC), datetime(2026, 12, 21, tzinfo=UTC),
            None, None, {"client": client, "notice_match": {
                "basis": match["basis"], "release_id": match["release_id"],
                "gacha_id": match["gacha_id"]}, "notice_id": match["notice_id"],
                "notice_revision": match["notice_revision"]})
        runs.append(EventRun(event, occurrence))
    repo = AsyncMock(spec=EventRepository)
    async def read_runs(**kwargs):
        return [run for run in runs if kwargs["event_id"] is None or run.event.id == kwargs["event_id"]]
    repo.runs.side_effect = read_runs
    repo.pool_occurrences.return_value = [(run.event.id, run.occurrence.id) for run in runs]
    for name in ("announcements", "notice_snapshots", "reward_nodes", "media", "spine"):
        getattr(repo, name).return_value = []
    service = EventService(repo, lambda value: value, lambda value: value)
    calendar = (await service.browse("europe", summary=True))["events"]
    assert len(calendar) == 1 and len(calendar[0]["selectable_members"]) == 4
    repo.runs.reset_mock()
    detail = await service.detail(2, "europe", 6302)
    assert all(call.kwargs["event_id"] is not None for call in repo.runs.await_args_list)
    repo.pool_occurrences.assert_awaited_once()
    assert detail["id"] == 2 and detail["occurrence_id"] == 6302
    assert detail["featured_names"] == ["Camellya", "Roccia", "Phoebe", "Brant"]
    assert len(detail["selectable_members"]) == 4


@pytest.mark.asyncio
async def test_projection_builder_retains_exact_pool_evidence(monkeypatch):
    from wuwa_story.read_models import source_sections

    source = {"notice_match": {"basis": "gacha_pool", "release_id": 22, "gacha_id": 100096},
              "notice_id": 42, "notice_revision": "a" * 64}
    event = SimpleNamespace(id=1, source="wuwa-game-client", title="Banner", slug=None,
                            event_kind="banner", description=None, source_url="https://example.test")
    session = SimpleNamespace(get=AsyncMock(return_value=event), execute=AsyncMock(return_value=[
        (6301, "europe", None, "notice:42@update", None, "end", source)]))
    async def browse(server, kind, version, locale, session, *, event_id,
                     occurrence_id=None, occurrence_ids=None):
        return {"events": [{"id": event_id, "occurrence_id": identity, "title": "Banner"}
                           for identity in occurrence_ids or [occurrence_id]]}
    monkeypatch.setattr(source_sections, "browse_events", browse)
    payload = await source_sections._event_payload(session, 1, "en", "current")
    assert payload["occurrences"][0]["_pool_match"] == {
        **source["notice_match"], "notice_id": 42, "notice_revision": "a" * 64}
    assert source_sections._calendar_payload(payload)["occurrences"][0]["_pool_match"] == payload["occurrences"][0]["_pool_match"]


@pytest.mark.asyncio
@pytest.mark.parametrize("notice", [
    {"revision": "b" * 64, "related_event_ids": [1, 2, 3, 4]},
    {"revision": "a" * 64, "related_event_ids": [1] * 65},
    {"revision": "a" * 64, "related_event_ids": [1, 1, 2]},
])
async def test_detail_fails_closed_when_notice_membership_is_not_proven(monkeypatch, notice):
    row = {**choices()[0], "server": "europe"}
    async def page(_, kind, identity, *args, **kwargs):
        return notice if kind == "game_notice" else {"event": row, "occurrences": [row]}
    page_mock = AsyncMock(side_effect=page)
    monkeypatch.setattr(reader.entities, "page_payload", page_mock)
    scope = AsyncMock(side_effect=AssertionError("must not load full calendar"))
    monkeypatch.setattr(reader.entities, "_scope_entries", scope)
    result = await reader.event_detail(None, 1, "europe", 6301)
    assert "selectable_members" not in result
    assert page_mock.await_count == 2
    scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_pool_query_uses_notice_members_and_exact_provenance():
    from unittest.mock import Mock

    from sqlalchemy.dialects import postgresql

    from wuwa_story.db.repositories.event_browsing import SqlAlchemyEventRepository

    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        revision="a" * 64, related_event_ids=[1, 2, 3, 4])),
        execute=AsyncMock(return_value=Mock(all=Mock(return_value=[
            (1, 6301), (2, 6302), (3, 6303), (4, 6304)]))))
    repository = SqlAlchemyEventRepository(session, "bucket")
    proof = choices()[0]["_pool_match"]
    assert await repository.pool_occurrences(server="europe", proof=proof,
        starts_at="start", ends_at="end") == [(1, 6301), (2, 6302), (3, 6303), (4, 6304)]
    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert [1, 2, 3, 4] in compiled.params.values()
    assert {"europe", "start", "end", 22, 100096, 42, "a" * 64} <= set(
        value for value in compiled.params.values() if isinstance(value, (str, int)))
    session.execute.return_value.all.return_value = [(1, 6301), (2, 6302), (3, 6303)]
    assert await repository.pool_occurrences(server="europe", proof=proof,
        starts_at="start", ends_at="end") == []
    session.get.return_value.revision = "b" * 64
    session.execute.reset_mock()
    assert await repository.pool_occurrences(server="europe", proof=proof,
        starts_at="start", ends_at="end") == []
    session.execute.assert_not_awaited()
