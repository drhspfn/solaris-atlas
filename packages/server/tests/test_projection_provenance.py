"""Explicit raw resources are loaded by deduplicated primary keys after paging."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from wuwa_story.read_models.readers import entities, provenance


@pytest.fixture(autouse=True)
def coverage(monkeypatch):
    monkeypatch.setattr(
        entities,
        "_coverage",
        AsyncMock(return_value=SimpleNamespace(published_generation=1, status="pending")),
    )
    monkeypatch.setattr(entities, "_locale_id", AsyncMock(return_value=1))


def resource_rows(rows):
    return Mock(all=Mock(return_value=rows))


def line(identity, record=70, action=80):
    return {
        "id": f"talk_item:{identity}",
        "text": {"content": "Text"},
        "provenance": {"source_file": "flow.json", "source_row": 0},
        "action": {"id": 1, "name": "ShowTalk", "index": 0, "params": None},
        "player_choices": [],
        "raw_refs": {
            "source_record_id": record,
            "action_node_id": action,
            "choice_source_record_ids": {},
        },
    }


@pytest.mark.asyncio
async def test_raw_endpoint_reads_one_source_primary_key_without_graph_joins():
    session = SimpleNamespace(scalar=AsyncMock(return_value={"Id": 7}))
    cached = {"canonical_key": "speaker:7", "source_record": {"id": 70, "source_file": "flow.json"}}
    response = await provenance.source_record(session, cached)
    assert response["source_record"]["raw_record"] == {"Id": 7}
    assert "raw_record" not in cached["source_record"]
    session.scalar.assert_awaited_once()
    statement = session.scalar.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "SELECT raw.source_record.data" in sql and "source_record.id =" in sql
    assert "JOIN" not in sql and "graph." not in sql


@pytest.mark.asyncio
async def test_two_lines_share_one_raw_resource_fetch_and_no_projected_raw_copy():
    raw, params = {"TalkItems": [1, 2]}, {"TalkItems": ["raw action"]}
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[resource_rows([(70, raw)]), resource_rows([(80, params)])])
    )
    compact = [line(1), line(2)]
    response = await provenance.hydrate_lines(session, compact, 1, "3.10")
    assert session.execute.await_count == 2
    assert response[0]["provenance"]["raw_record"] is response[1]["provenance"]["raw_record"]
    assert response[0]["action"]["params"] is response[1]["action"]["params"]
    assert "raw_record" not in compact[0]["provenance"] and compact[0]["action"]["params"] is None
    assert "raw_refs" not in response[0]
    for call in session.execute.call_args_list:
        compiled = call.args[0].compile(dialect=postgresql.dialect())
        assert "JOIN" not in str(compiled) and "graph." not in str(compiled)
        assert all(len(value) == 1 for value in compiled.params.values() if isinstance(value, list))


@pytest.mark.asyncio
async def test_raw_transcript_pages_before_resource_hydration(monkeypatch):
    compact = [line(i, record=100 + i, action=200 + i) for i in range(300)]
    monkeypatch.setattr(entities, "quest_section", AsyncMock(return_value={"lines": compact}))
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[resource_rows([(200, {"Id": 200})]), resource_rows([(300, {})])]
        )
    )
    response = await entities.transcript_page(
        session, "139000027", limit=1, offset=100, include_raw=True
    )
    assert len(response["lines"]) == 1 and response["lines"][0]["id"] == "talk_item:100"
    assert session.execute.await_count == 2
    assert list(session.execute.call_args_list[0].args[0].compile().params.values()) == [[200]]
    assert list(session.execute.call_args_list[1].args[0].compile().params.values()) == [[300]]


@pytest.mark.asyncio
async def test_nonraw_transcript_never_fetches_resource_tables(monkeypatch):
    monkeypatch.setattr(entities, "quest_section", AsyncMock(return_value={"lines": [line(1)]}))
    response = await entities.transcript_page(SimpleNamespace(), 7, include_raw=False)
    assert "raw_refs" not in response["lines"][0]
    assert response["lines"][0]["action"]["params"] is None


@pytest.mark.asyncio
async def test_missing_raw_pointers_do_not_trigger_source_graph_fallback():
    compact = line(1)
    del compact["raw_refs"]
    session = SimpleNamespace(execute=AsyncMock(return_value=resource_rows([])))
    with pytest.raises(HTTPException) as error:
        await provenance.hydrate_lines(session, [compact], 1, "current")
    assert error.value.status_code == 503
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "card[" in sql and ".payload" not in sql and "raw.source_record" not in sql
