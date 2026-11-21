"""Unit tests for read projection SQL optimizations, cancellation safety, and fair scheduling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from wuwa_story.db.repositories.character_identity import dialogue_participation, speaker_crosswalk
from wuwa_story.db.repositories.quest_scope import quest_state_links
from wuwa_story.read_models import refresh, source_relations, worker


def _compile_pg(statement) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def test_speaker_crosswalk_uses_single_join_and_pushes_down_speaker_ids():
    subq = speaker_crosswalk(22, speaker_ids=[42, 99])
    sql = _compile_pg(select(subq))
    assert "GROUP BY graph.edge.from_node_id, graph.edge.to_node_id, graph.edge.id" in sql
    assert "graph.edge.from_node_id IN (42, 99)" in sql
    assert "EXISTS (" not in sql


def test_dialogue_participation_removes_redundant_spoken_exists_and_supports_lightweight_mode():
    full_sql = _compile_pg(select(dialogue_participation(22, speaker_ids=[376378])))
    assert "participation_proof.edge_id = participation_spoken.id" in full_sql
    assert "graph.edge_evidence.edge_id = participation_spoken.id" not in full_sql
    assert "raw.source_record" in full_sql

    light_sql = _compile_pg(
        select(dialogue_participation(22, speaker_ids=[376378], include_source_record=False))
    )
    assert "raw.source_record" not in light_sql
    assert "GROUP BY" not in light_sql


def test_dialogue_participation_uses_release_cte_for_large_multistate_scans():
    many_states = list(range(1, 250))
    sql = _compile_pg(
        select(dialogue_participation(22, state_ids=many_states, include_source_record=False))
    )
    assert "WITH participation_rel_ev AS MATERIALIZED" in sql
    assert "raw.source_record" not in sql


def test_quest_state_links_filters_relation_type_inside_endpoints_cte():
    subq = quest_state_links(22, state_ids=[10, 20, 30])
    sql = _compile_pg(select(subq))
    assert "WITH state_direct_refs_endpoints AS MATERIALIZED" in sql
    # Relation filter must live inside the materialized endpoints CTE so PostgreSQL uses ix_edge_to_relation.
    endpoints_block = sql.split("state_direct_refs AS MATERIALIZED")[0]
    assert "graph.edge.relation_type_id IN" in endpoints_block
    assert "graph.edge.to_node_id IN (10, 20, 30)" in endpoints_block


@pytest.mark.asyncio
async def test_dialogue_filters_pushes_speaker_id_into_speaker_crosswalk():
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(one_or_none=lambda: (376378, 900)),
                [("character:1502", "15502-rover-spectro", 15502)],
            ]
        ),
        scalars=AsyncMock(return_value=[1001]),
    )
    result = await source_relations._dialogue_filters(session, 105812, 22)
    assert result == {"character_keys": ["character:1502"], "quest_ids": [1001]}
    crosswalk_sql = _compile_pg(session.execute.await_args_list[1].args[0])
    assert "graph.edge.from_node_id IN (376378)" in crosswalk_sql


@pytest.mark.asyncio
async def test_safe_close_session_shields_teardown_and_consumes_connection_errors():
    close_started = asyncio.Event()
    allow_close_finish = asyncio.Event()

    class FlakySession:
        async def close(self):
            close_started.set()
            await allow_close_finish.wait()
            raise ConnectionError("unexpected connection_lost() call")

    session = FlakySession()
    task = asyncio.create_task(refresh._safe_close_session(session))
    await close_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Unblock background close task; its ConnectionError must be consumed cleanly.
    allow_close_finish.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_run_claim_survives_fail_claim_error_after_deadline(monkeypatch, caplog):
    claim = SimpleNamespace(kind="character", entity_id=15502, snapshot_key="current")

    async def hang(_factory, _claim):
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "refresh_claim", hang)
    monkeypatch.setattr(worker, "fail_claim", AsyncMock(side_effect=ConnectionError("pool closed")))
    # Must log and return cleanly without crashing the worker batch.
    await worker._run_claim(object(), claim, claim_timeout_seconds=0.01)
    assert "read_projection.fail_claim_failed" in caplog.text


@pytest.mark.asyncio
async def test_watch_prioritizes_fast_kinds_before_bulk_dialogue_and_boosts_bulk_batch(monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(
            read_projection_min_free_gib=0,
            read_projection_batch_limit=40,
            read_projection_batch_concurrency=8,
            read_projection_poll_seconds=0.01,
        ),
    )
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=100))
    monkeypatch.setattr(worker, "reconcile_ready_scopes", AsyncMock(return_value=0))

    pending = {"character": 1, "cutscene": 1, "dialogue": 2}
    calls = []

    async def fake_refresh(_factory, *, kinds, **filters):
        kind = kinds[0]
        calls.append((kind, filters.get("limit")))
        if pending.get(kind, 0) > 0:
            pending[kind] -= 1
            # Re-dirty character while dialogue is draining to verify preemption.
            if kind == "dialogue" and pending["dialogue"] == 1:
                pending["character"] = 1
            if sum(pending.values()) == 0:
                stop.set()
            return 1
        return 0

    monkeypatch.setattr(worker, "refresh_batch", fake_refresh)
    await worker.watch_refreshes(stop, kinds=["dialogue", "character", "cutscene"])

    # Priority kinds drain before dialogue is touched; dialogue runs with boosted bulk limit;
    # and newly arriving character work preempts remaining dialogue backlog.
    worked_order = [
        (kind, limit)
        for kind, limit in calls
        if not (kind in {"character", "cutscene"} and calls.count((kind, limit)) > 0)
    ]
    assert worked_order is not None  # sanity
    first_dialogue_idx = next(i for i, (k, _) in enumerate(calls) if k == "dialogue")
    before_dialogue = [k for k, _ in calls[:first_dialogue_idx]]
    assert "character" in before_dialogue and "cutscene" in before_dialogue
    dialogue_calls = [(k, lim) for k, lim in calls if k == "dialogue"]
    assert all(lim == worker.BULK_BATCH_LIMIT for _, lim in dialogue_calls)
    # After first dialogue call re-dirtied character, character ran before second dialogue call.
    second_dialogue_idx = [i for i, (k, _) in enumerate(calls) if k == "dialogue"][1]
    between = [k for k, _ in calls[first_dialogue_idx + 1 : second_dialogue_idx]]
    assert "character" in between


def test_prod_compose_enables_all_projection_kinds_for_worker():
    from pathlib import Path

    compose_path = Path(__file__).resolve().parents[3] / "infrastructure" / "prod" / "compose.yml"
    content = compose_path.read_text(encoding="utf-8")
    assert "READ_PROJECTION_KINDS: ${READ_PROJECTION_WORKER_KINDS:-}" in content
