"""Background shared scenes retain compact output without needless raw hydration."""

from contextlib import nullcontext
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import defer

from wuwa_story.api.routes.story import profiles
from wuwa_story.db.models.core import DialogueLine, QuestAction, QuestState
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.read_models.context import SOURCE_BUILD, source_build
from wuwa_story.read_models.source_sections import _compact_storage
from wuwa_story.story import service


class DeferredAction(Row):
    @property
    def params(self):
        assert self.loaded, "Full action params accessed without a required hydration"
        return self.value


class DeferredRecord(Row):
    @property
    def data(self):
        assert self.loaded, "Raw source body accessed during compact rendering"
        return self.value


def compact(lines):
    # Match the actual builder path; params compaction is path-sensitive.
    return _compact_storage(
        {
            "payload": {
                "profile": {"co_present_characters": [{"shared_scenes": [{"dialogue": lines}]}]}
            }
        }
    )


def scene_statement():
    return (
        select(DialogueLine, QuestAction, QuestState, Node, SourceRecord, SourceFile)
        .options(
            defer(QuestAction.params),
            defer(QuestAction.metadata_json),
            defer(QuestState.metadata_json),
            defer(Node.metadata_json),
            defer(SourceRecord.data),
            defer(SourceFile.metadata_json),
            defer(DialogueLine.metadata_json),
        )
        .select_from(DialogueLine)
        .join(Node, Node.id == DialogueLine.node_id)
        .join(QuestAction, QuestAction.node_id == DialogueLine.action_node_id)
        .join(QuestState, QuestState.node_id == QuestAction.quest_state_node_id)
        .outerjoin(SourceRecord, SourceRecord.id == DialogueLine.source_record_id)
        .outerjoin(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(QuestState.node_id == 7)
        .order_by(QuestAction.action_index, DialogueLine.source_index, DialogueLine.node_id)
        .limit(80)
    )


@pytest.mark.asyncio
async def test_source_build_shared_scenes_preserve_exact_compacted_payload_and_choices(monkeypatch):
    branch_params = {
        "TalkItems": [{"Id": 100}, {"Id": 101}, {"Id": 102}],
        "TalkSequence": [[100], [101, 102]],
    }
    source_file = Row(logical_source_path="BinData/flow/state.json")
    choices = [
        Row(node_id=i, localization_key_id=i, metadata_json={"choice_index": i})
        for i in (31, 32, 33, 34)
    ]
    # A direct target, a nested target, an ambiguous target and an unresolved target.
    direct = "talk_item:state:0:1"
    nested = "talk_item:state:0:2"
    targets = AsyncMock(return_value=[(31, direct), (33, direct), (33, nested)])
    nested_actions = AsyncMock(return_value=[(32, 900)])
    nested_targets = AsyncMock(return_value=[(900, nested)])
    monkeypatch.setattr(repository, "choice_targets", targets)
    monkeypatch.setattr(repository, "nested_actions", nested_actions)
    monkeypatch.setattr(repository, "nested_targets", nested_targets)
    monkeypatch.setattr(service, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(service, "prefetch_localizations", AsyncMock())
    monkeypatch.setattr(
        service, "_node_label", AsyncMock(return_value={"canonical_key": "speaker:1"})
    )
    monkeypatch.setattr(
        service,
        "_localized",
        AsyncMock(
            side_effect=lambda _s, identity, _locale, _release: {"content": f"Text {identity}"}
        ),
    )
    prefetch = AsyncMock(wraps=service.prefetch_dialogue)
    render = AsyncMock(wraps=service._dialogue_payload)
    monkeypatch.setattr(profiles, "prefetch_dialogue", prefetch)
    monkeypatch.setattr(profiles, "_dialogue_payload", render)
    results = []
    for background in (False, True):
        prefetch.reset_mock()
        render.reset_mock()
        actions = [
            DeferredAction(
                node_id=i,
                action_id=f"action:{i}",
                action_name="ShowTalk",
                action_index=0,
                value=value,
                loaded=False,
            )
            for i, value in enumerate((branch_params, {"TalkItems": ["large"] * 1000}, None, {}), 1)
        ]
        records = [
            DeferredRecord(
                id=i,
                row_index=i,
                source_key=str(i),
                value=None if i == 3 else {"body": "x" * 100_000},
                loaded=False,
            )
            for i in range(1, 5)
        ]
        choice_record = DeferredRecord(
            id=50,
            row_index=50,
            source_key="choice",
            value={"choice": "body"},
            loaded=not background,
        )
        choice_rows = [
            (1, choice, f"choice:{choice.node_id}", choice_record, source_file)
            for choice in choices
        ]
        monkeypatch.setattr(repository, "dialogue_choices", AsyncMock(return_value=choice_rows))

        async def load_actions(_session, identities, actions=actions):
            for action in actions:
                if action.node_id in identities:
                    action.loaded = True

        async def load_records(_session, identities, records=records):
            for record in records:
                if record.id in identities:
                    record.loaded = True

        load_action_params = AsyncMock(side_effect=load_actions)
        load_raw_records = AsyncMock(side_effect=load_records)
        monkeypatch.setattr(repository, "load_action_params", load_action_params)
        monkeypatch.setattr(repository, "load_raw_records", load_raw_records)
        rows = []
        for index, (action, record) in enumerate(zip(actions, records, strict=True)):
            row = (
                Row(
                    node_id=index + 1,
                    speaker_node_id=1,
                    localization_key_id=index + 10,
                    inline_text=None,
                    game_talk_item_id=index,
                    game_text_id=index,
                    source_type="talk_item",
                    source_index=index,
                ),
                action,
                Row(state_key="state"),
                Row(canonical_key=f"talk_item:state:0:{index}"),
                record,
                source_file,
            )
            rows.append((*row, action.value is None) if background else row)
        session = Row(info={}, execute=AsyncMock(return_value=Mock(all=Mock(return_value=rows))))
        with source_build() if background else nullcontext():
            fetched, lines = await profiles._shared_scene_dialogue(
                session, scene_statement(), "en", 12
            )
        assert not SOURCE_BUILD.get()
        assert len(fetched) == len(lines) == 4
        assert all(len(row) == 6 for row in fetched)
        assert all(
            call.kwargs["include_raw"] is (not background) for call in prefetch.await_args_list
        )
        assert all(
            call.kwargs["include_raw"] is (not background) for call in render.await_args_list
        )
        assert [choice["target_line_id"] for choice in lines[0]["player_choices"]] == [
            direct,
            nested,
            None,
            None,
        ]
        assert lines[0]["player_choices"][0]["branch"] == {
            "line_ids": [direct, nested],
            "continuation_line_id": None,
        }
        assert lines[0]["player_choices"][1]["branch"]["line_ids"] == [nested]
        assert lines[0]["player_choices"][2]["branch"] is None
        if background:
            load_raw_records.assert_not_awaited()
            load_action_params.assert_awaited_once_with(session, [1])
            assert not any(record.loaded for record in records)
            assert "params" not in lines[0]["action"]
            assert "params" not in lines[1]["action"]
            assert lines[2]["action"]["params"] is None  # Genuine JSON null is retained.
            assert "params" not in lines[3]["action"]  # Empty object is still non-null.
        else:
            load_raw_records.assert_awaited_once_with(session, [1, 2, 3, 4])
            load_action_params.assert_awaited_once_with(session, [1, 2, 3, 4])
            assert "raw_record" in lines[0]["provenance"]
            assert "raw_record" in lines[0]["player_choices"][0]["provenance"]
            assert lines[2]["action"]["params"] is None
        sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
        assert "raw.source_record.data" not in sql
        assert "core.quest_action.params," not in sql
        assert (
            "core.quest_action.params IS NULL" in sql
            if background
            else "core.quest_action.params" not in sql
        )
        results.append(compact(lines))
    assert results[0] == results[1]
    assert targets.await_count == nested_actions.await_count == nested_targets.await_count == 2
