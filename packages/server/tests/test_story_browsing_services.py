"""Regressions for request batching, localization provenance and compact transcripts."""

import inspect
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes.story import transcripts
from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.story import service, usecases
from wuwa_story.story.domain import localization_payload


def versions():
    # Import sequence deliberately disagrees with game chronology.
    return {
        1: Row(id=1, game_version="3.9", sequence=99),
        2: Row(id=2, game_version="3.10", sequence=2),
        3: Row(id=3, game_version="3.11", sequence=3),
    }


def text(release_id, content, status="resolved_nonempty", key_id=1):
    return Row(
        key_id=key_id,
        release_id=release_id,
        content=content,
        status=status,
        source_record_id=release_id * 10,
    )


@pytest.mark.parametrize(
    "exact", [None, text(2, "", "resolved_empty"), text(2, None, "missing_key")]
)
def test_localization_fallback_keeps_requested_status_and_source_provenance(exact):
    releases = versions()
    values = [text(3, "future"), text(1, "previous")]
    if exact is not None:
        values.append(exact)
    result = localization_payload("quest.name", "ja", releases[2], values, releases)
    assert result == {
        "key": "quest.name",
        "locale": "ja",
        "content": "previous",
        "resolution": "resolved_nonempty",
        "requested_resolution": exact.status if exact else "missing_key",
        "fallback": True,
        "source_game_version": "3.9",
        "source_record_id": 10,
    }


def test_localization_prefers_exact_and_never_uses_future_text():
    releases = versions()
    result = localization_payload(
        "name", "en", releases[2], [text(1, "old"), text(2, "exact"), text(3, "future")], releases
    )
    assert (result["content"], result["fallback"], result["source_record_id"]) == (
        "exact",
        False,
        20,
    )
    missing = localization_payload("name", "en", releases[2], [text(3, "future")], releases)
    assert (missing["content"], missing["resolution"], missing["fallback"]) == (
        None,
        "missing_key",
        False,
    )


@pytest.mark.parametrize(
    "key,available,requested", [(None, True, 2), ("name", False, 2), ("name", True, None)]
)
def test_unavailable_localization_remains_distinct_from_missing_key(key, available, requested):
    releases = versions()
    assert localization_payload(
        key, "xx", releases.get(requested), [], releases, available=available
    ) == {
        "key": key,
        "locale": "xx",
        "content": None,
        "resolution": "unavailable",
    }


@pytest.mark.asyncio
async def test_localizations_batch_unique_keys_and_cache_only_within_one_request(monkeypatch):
    locale = AsyncMock(return_value=7)
    release_rows = AsyncMock(return_value=list(versions().values()))
    keys = AsyncMock(side_effect=lambda _session, ids: {i: f"key:{i}" for i in ids})
    values = AsyncMock(
        side_effect=lambda _session, ids, _locale: [text(1, f"old:{i}", key_id=i) for i in ids]
    )
    for name, mock in [
        ("locale_id", locale),
        ("fetch_releases", release_rows),
        ("localization_keys", keys),
        ("localization_values", values),
    ]:
        monkeypatch.setattr(repository, name, mock)
    session = Row(info={})
    ids = list(range(1, 502))
    await service.prefetch_localizations(session, ids + ids + [None], "ja", 2)
    assert [len(call.args[1]) for call in values.await_args_list] == [500, 1]
    assert len(session.info["wuwa_localized_cache"]) == 501
    assert session.info["wuwa_localized_cache"][(501, "ja", 2)]["source_record_id"] == 10
    await service.prefetch_localizations(session, ids, "ja", 2)
    assert values.await_count == 2
    assert locale.await_count == release_rows.await_count == 1
    await service.prefetch_localizations(Row(info={}), [1], "ja", 2)
    assert values.await_count == 3
    assert locale.await_count == release_rows.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("include_raw", [True, False])
async def test_dialogue_hydrates_shared_json_once_across_batches_and_repeated_rows(
    monkeypatch, include_raw
):
    choices = AsyncMock(return_value=[])
    actions = AsyncMock()
    records = AsyncMock()
    monkeypatch.setattr(repository, "dialogue_choices", choices)
    monkeypatch.setattr(repository, "load_action_params", actions)
    monkeypatch.setattr(repository, "load_raw_records", records)
    monkeypatch.setattr(service, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(service, "prefetch_localizations", AsyncMock())
    action, record = Row(node_id=8), Row(id=9)
    rows = [
        (
            Row(node_id=i, speaker_node_id=None, localization_key_id=None),
            action,
            None,
            None,
            record,
            None,
        )
        for i in range(1, 502)
    ]
    session = Row(info={})
    await service.prefetch_dialogue(session, rows + rows, "en", 2, include_raw=include_raw)
    assert [len(call.args[1]) for call in choices.await_args_list] == [500, 1]
    if include_raw:
        actions.assert_awaited_once_with(session, [8])
        records.assert_awaited_once_with(session, [9])
    else:
        actions.assert_not_awaited()
        records.assert_not_awaited()
    await service.prefetch_dialogue(session, rows, "en", 2, include_raw=include_raw)
    assert choices.await_count == 2
    assert actions.await_count == records.await_count == int(include_raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_option", [None, False, True])
async def test_transcript_default_is_raw_and_compact_preserves_authored_choice_branch(
    monkeypatch, raw_option
):
    params = {"TalkItems": [{"Id": 100}, {"Id": 101}], "TalkSequence": [[100, 101]]}
    action = Row(node_id=8, action_id=80, action_name="talk", action_index=0)
    line = Row(
        node_id=1,
        speaker_node_id=None,
        localization_key_id=None,
        inline_text="Hello",
        game_talk_item_id=100,
        game_text_id=None,
        source_type="talk",
        source_index=0,
    )
    choice = Row(node_id=12, localization_key_id=None, metadata_json={"choice_index": 0})
    record = Row(id=9, row_index=4, source_key="row", data={"large": "source"})
    source_file = Row(logical_source_path="quest.json")
    row = (
        line,
        action,
        Row(state_key="flow:1"),
        Row(canonical_key="talk_item:flow:1:0:0"),
        record,
        source_file,
    )
    session = Row(info={})
    monkeypatch.setattr(
        usecases, "get_quest", AsyncMock(return_value=Row(node_id=7, game_quest_id=70))
    )
    monkeypatch.setattr(usecases, "_release_id", AsyncMock(return_value=2))
    monkeypatch.setattr(usecases, "_quest_state_ids", AsyncMock(return_value=[3]))
    monkeypatch.setattr(usecases, "authored_order", AsyncMock(return_value=None))
    monkeypatch.setattr(usecases, "_quest_info", AsyncMock(return_value={"game_quest_id": 70}))
    monkeypatch.setattr(usecases, "dialogue_media", AsyncMock(return_value={}))
    monkeypatch.setattr(repository, "quest_transcript_rows", AsyncMock(return_value=([row], 0)))
    monkeypatch.setattr(repository, "quest_scenes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        repository,
        "dialogue_choices",
        AsyncMock(return_value=[(1, choice, "choice:12", record, source_file)]),
    )
    monkeypatch.setattr(
        repository, "choice_targets", AsyncMock(return_value=[(12, "talk_item:flow:1:0:0")])
    )

    # Branch resolution needs params even in compact mode, but only one hydration.
    async def hydrate(_session, identities):
        assert identities == [8]
        action.params = params

    actions = AsyncMock(side_effect=hydrate)
    records = AsyncMock()
    monkeypatch.setattr(repository, "load_action_params", actions)
    monkeypatch.setattr(repository, "load_raw_records", records)
    options = {} if raw_option is None else {"include_raw": raw_option}
    result = await usecases.quest_transcript(session, "quest:70", **options)
    payload = result["lines"][0]
    assert payload["player_choices"][0]["branch"] == {
        "line_ids": ["talk_item:flow:1:0:0", "talk_item:flow:1:0:1"],
        "continuation_line_id": None,
    }
    assert payload["text"] == {"inline_text": "Hello", "resolution": "inline"}
    assert payload["provenance"]["source_file"] == "quest.json"
    is_raw = raw_option is not False
    assert ("raw_record" in payload["provenance"]) is is_raw
    assert ("raw_record" in payload["player_choices"][0]["provenance"]) is is_raw
    assert payload["action"]["params"] == (params if is_raw else None)
    actions.assert_awaited_once()
    assert records.await_count == int(is_raw)
    assert inspect.signature(transcripts.quest_transcript).parameters["include_raw"].default is True


@pytest.mark.asyncio
async def test_localization_and_scene_reads_use_joined_projection_without_per_row_lookups():
    session = Row(execute=AsyncMock(return_value=[]))
    await repository.localization_values(session, [1, 2], 7)
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN i18n.localization_content" in sql
    assert "(SELECT" not in sql
    await repository.quest_scenes(session, 7)
    statement = session.execute.await_args.args[0]
    assert "canonical_key" in statement.selected_columns.keys()
    # The fake session deliberately has no get(): scene keys must be in this read.
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_quest_profile_resolves_snapshot_before_loading_owned_states(monkeypatch):
    quest = Row(node_id=7, game_quest_id=70)
    session = Row(info={})
    release = AsyncMock(return_value=13)
    monkeypatch.setattr(service, "_release_id", release)
    for name in ("quest_node_rows", "quest_hierarchy_rows", "quest_states", "quest_actions",
                 "quest_scenes", "quest_requirement_records"):
        monkeypatch.setattr(repository, name, AsyncMock(return_value=[]))

    async def states(_session, quest_id, release_id):
        release.assert_awaited_once_with(session, "2.8.0")
        assert (quest_id, release_id) == (7, 13)
        return []

    monkeypatch.setattr(service, "_quest_state_ids", states)
    monkeypatch.setattr(service, "_explicit_entity_links", AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_quest_info", AsyncMock(return_value={"game_quest_id": 70}))
    monkeypatch.setattr(service, "_latest_source_record", AsyncMock(return_value=None))
    result = await service.quest_profile_payload(session, quest, game_version="2.8.0")
    assert result["quest"]["game_quest_id"] == 70
    assert result["flow_states"] == []
