import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from wuwa_story.read_models import worker
from wuwa_story.read_models.context import SOURCE_BUILD, source_build


def test_source_context_is_nested_and_restored_after_failure():
    assert not SOURCE_BUILD.get()
    with source_build():
        with pytest.raises(RuntimeError), source_build():
            assert SOURCE_BUILD.get()
            raise RuntimeError("source unavailable")
        assert SOURCE_BUILD.get()
    assert not SOURCE_BUILD.get()


@pytest.mark.asyncio
async def test_failed_entity_does_not_stop_other_durable_refreshes(monkeypatch):
    claims = [SimpleNamespace(kind="character", entity_id=i, snapshot_key="current") for i in (1, 2)]
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=claims))
    contexts = []

    async def refresh(factory, claim):
        contexts.append(SOURCE_BUILD.get())
        if claim.entity_id == 1:
            raise RuntimeError("temporary source error")

    monkeypatch.setattr(worker, "refresh_claim", refresh)
    assert await worker.refresh_batch(object(), limit=2) == 2
    assert contexts == [True, True]
    assert not SOURCE_BUILD.get()


@pytest.mark.asyncio
async def test_shutdown_cancels_build_instead_of_hiding_cancellation(monkeypatch):
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=[object()]))
    monkeypatch.setattr(worker, "refresh_claim", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await worker.refresh_batch(object())
    assert not SOURCE_BUILD.get()


@pytest.mark.asyncio
async def test_watch_keeps_pending_work_when_disk_headroom_is_low(monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(read_projection_min_free_gib=20)
    )
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024))
    refresh = AsyncMock()
    monkeypatch.setattr(worker, "refresh_batch", refresh)

    async def finish_wait(awaitable, *, timeout):
        stop.set()
        await awaitable

    monkeypatch.setattr(worker.asyncio, "wait_for", finish_wait)
    await worker.watch_refreshes(stop)
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_watch_passes_selected_kinds_to_refresh(monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(read_projection_min_free_gib=20)
    )
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))

    async def refresh(factory, *, kinds):
        assert kinds == ["character"]
        stop.set()
        return 1

    monkeypatch.setattr(worker, "refresh_batch", refresh)
    await worker.watch_refreshes(stop, kinds=["character"])


@pytest.mark.asyncio
async def test_watch_rotates_busy_kinds_and_skips_empty_queues(monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(read_projection_min_free_gib=0))
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    calls = []

    async def refresh(factory, *, kinds, **filters):
        calls.append((kinds[0], filters))
        if len(calls) == 5:
            stop.set()
        return int(kinds[0] != "empty")

    monkeypatch.setattr(worker, "refresh_batch", refresh)
    await worker.watch_refreshes(stop, kinds=["character", "empty", "game_event"], snapshot_key="current")
    assert [kind for kind, _ in calls] == ["character", "empty", "game_event", "character", "empty"]
    assert all(filters == {"snapshot_key": "current"} for _, filters in calls)


@pytest.mark.asyncio
async def test_estimate_is_read_only_and_does_not_run_refresh(monkeypatch, capsys):
    from wuwa_story.read_models.refresh import BackfillEstimate

    estimate = BackfillEstimate("character", "current", 1, 2, 2, 4096, 1, 1024)
    monkeypatch.setattr(worker, "estimate_backfill", AsyncMock(return_value=estimate))
    refresh = AsyncMock()
    monkeypatch.setattr(worker, "refresh_batch", refresh)
    monkeypatch.setattr(worker, "engine", SimpleNamespace(dispose=AsyncMock()))
    await worker.main(SimpleNamespace(kind=["character"], snapshot="current", estimate=True))
    assert '"estimated_bytes": 4096' in capsys.readouterr().out
    refresh.assert_not_awaited()
    worker.engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_pilot_requires_one_kind_and_valid_entity_id(monkeypatch):
    monkeypatch.setattr(worker, "engine", SimpleNamespace(dispose=AsyncMock()))
    with pytest.raises(ValueError, match="exactly one"):
        await worker.main(
            SimpleNamespace(
                kind=None,
                snapshot="current",
                estimate=False,
                entity_id=[7],
                backfill=True,
            )
        )
    with pytest.raises(ValueError, match="positive"):
        await worker.main(
            SimpleNamespace(
                kind=["character"], snapshot="current", estimate=False,
                entity_id=[0], backfill=True,
            )
        )


@pytest.mark.asyncio
async def test_runtime_deadline_unwinds_build_before_recording_durable_failure(monkeypatch):
    claim = SimpleNamespace(kind="item", entity_id=9, snapshot_key="3.8.0")
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=[claim]))
    unwound = asyncio.Event()

    async def blocked(_factory, _claim):
        try:
            await asyncio.Event().wait()
        finally:
            unwound.set()

    async def fail(_factory, received, error):
        assert unwound.is_set()
        assert received is claim and isinstance(error, TimeoutError)

    failure = AsyncMock(side_effect=fail)
    monkeypatch.setattr(worker, "refresh_claim", blocked)
    monkeypatch.setattr(worker, "fail_claim", failure)
    assert await worker.refresh_batch(object(), claim_timeout_seconds=0.01) == 1
    failure.assert_awaited_once()
    assert not SOURCE_BUILD.get()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 503])
async def test_failure_logs_safe_http_status_and_snapshot_without_detail(monkeypatch, caplog, status):
    claim = SimpleNamespace(kind="character", entity_id=42, snapshot_key="3.8.0")
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=[claim]))
    monkeypatch.setattr(worker, "refresh_claim", AsyncMock(side_effect=HTTPException(status, "secret payload")))
    await worker.refresh_batch(object())
    record = caplog.records[-1]
    assert (record.projection_kind, record.projection_entity_id, record.projection_snapshot) == (
        "character", 42, "3.8.0"
    )
    assert record.projection_http_status == status and record.projection_sqlstate is None
    assert record.projection_timeout == "none" and "secret payload" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlstate", ["57014", "55P03", "40P01"])
async def test_failure_logs_wrapped_database_code_without_sql_or_credentials(monkeypatch, caplog, sqlstate):
    claim = SimpleNamespace(kind="quest", entity_id=99, snapshot_key="current")
    cause = Exception("secret asyncpg detail")
    cause.sqlstate = sqlstate
    wrapped = Exception("secret database message")
    wrapped.__cause__ = cause
    error = DBAPIError("SELECT secret_sql", {"password": "credential"}, wrapped)
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=[claim]))
    monkeypatch.setattr(worker, "refresh_claim", AsyncMock(side_effect=error))
    await worker.refresh_batch(object())
    record = caplog.records[-1]
    assert record.projection_sqlstate == sqlstate and record.projection_http_status is None
    assert record.projection_timeout == ("database_cancel_or_timeout" if sqlstate == "57014" else "none")
    assert all(secret not in caplog.text for secret in ("secret_sql", "credential", "secret database", "secret asyncpg"))


@pytest.mark.asyncio
async def test_claim_deadline_is_distinct_from_an_inner_timeout(monkeypatch, caplog):
    claim = SimpleNamespace(kind="quest", entity_id=7, snapshot_key="current")
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=[claim]))
    failure = AsyncMock()
    monkeypatch.setattr(worker, "fail_claim", failure)
    monkeypatch.setattr(worker, "refresh_claim", AsyncMock(side_effect=TimeoutError("secret detail")))
    await worker.refresh_batch(object())
    assert caplog.records[-1].projection_timeout == "none"
    failure.assert_not_awaited()  # refresh_claim persists ordinary failures itself.

    async def blocked(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "refresh_claim", blocked)
    await worker.refresh_batch(object(), claim_timeout_seconds=0.01)
    assert caplog.records[-1].projection_timeout == "claim_deadline"
    failure.assert_awaited_once()
    assert "secret detail" not in caplog.text


def test_failure_codes_reject_invalid_untrusted_values():
    error = DBAPIError("sql", {}, Exception("private"))
    error.orig.sqlstate = "57014 secret"
    assert worker._failure_codes(error) == (None, None)
    error.orig.sqlstate = "57014"
    assert worker._failure_codes(error) == (None, "57014")
    error.orig.sqlstate = None
    error.orig.__cause__ = error.orig  # Cycles from adapters must not hang diagnostics.
    assert worker._failure_codes(error) == (None, None)


def test_failure_log_rejects_untrusted_snapshot_text(caplog):
    worker._log_failure(SimpleNamespace(kind="quest", entity_id=1, snapshot_key="private\nsecret"),
                        ValueError("private detail"))
    assert caplog.records[-1].projection_snapshot == "other"
    assert "secret" not in caplog.text and "private detail" not in caplog.text


@pytest.mark.asyncio
async def test_one_shot_pilot_enqueues_without_debounce_and_claims_exact_scope(monkeypatch):
    session = AsyncMock()
    session.begin = Mock(return_value=AsyncMock())
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(worker, "SessionFactory", Mock(return_value=context))
    monkeypatch.setattr(worker, "engine", SimpleNamespace(dispose=AsyncMock()))
    enqueue, run = AsyncMock(), AsyncMock(return_value=1)
    monkeypatch.setattr(worker, "mark_dirty", enqueue)
    monkeypatch.setattr(worker, "refresh_batch", run)
    await worker.main(SimpleNamespace(
        kind=["item"], snapshot="current", estimate=False,
        entity_id=[9], backfill=True, watch=False,
    ))
    enqueue.assert_awaited_once_with(session, "item", [9], "current", debounce_seconds=0)
    run.assert_awaited_once_with(kinds=["item"], entity_ids=[9], snapshot_key="current")


@pytest.mark.asyncio
@pytest.mark.parametrize('shutdown_signal', [signal.SIGTERM, signal.SIGINT])
async def test_watch_signals_finish_current_refresh_then_exit_without_claiming_more(monkeypatch, shutdown_signal):
    loop = asyncio.get_running_loop()
    callbacks = {}
    monkeypatch.setattr(loop, 'add_signal_handler', lambda sig, callback: callbacks.setdefault(sig, callback))
    remove = Mock()
    monkeypatch.setattr(loop, 'remove_signal_handler', remove)
    monkeypatch.setattr(worker, 'get_settings', lambda: SimpleNamespace(
        read_projection_kinds=['quest'], read_projection_min_free_gib=1))
    monkeypatch.setattr(worker.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))
    entered, finish = asyncio.Event(), asyncio.Event()
    completed = []

    async def refresh(*args, **kwargs):
        entered.set()
        await finish.wait()
        completed.append(True)
        return 1

    refresh = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(worker, 'refresh_batch', refresh)
    dispose = AsyncMock()
    monkeypatch.setattr(worker, 'engine', SimpleNamespace(dispose=dispose))
    task = asyncio.create_task(worker.main(SimpleNamespace(
        kind=['quest'], snapshot='current', estimate=False, entity_id=[], backfill=False, watch=True)))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        callbacks[shutdown_signal]()
        await asyncio.sleep(0)
        assert not task.done()
        assert not completed
        dispose.assert_not_awaited()
        finish.set()
        await asyncio.wait_for(task, 1)
        assert completed == [True]
        refresh.assert_awaited_once()
        dispose.assert_awaited_once()
        assert {call.args[0] for call in remove.call_args_list} == {signal.SIGTERM, signal.SIGINT}
    finally:
        finish.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_watch_signal_wakes_idle_wait_and_removes_handlers(monkeypatch):
    loop = asyncio.get_running_loop()
    callbacks = {}
    monkeypatch.setattr(loop, 'add_signal_handler', lambda sig, callback: callbacks.setdefault(sig, callback))
    remove = Mock()
    monkeypatch.setattr(loop, 'remove_signal_handler', remove)
    monkeypatch.setattr(worker, 'engine', SimpleNamespace(dispose=AsyncMock()))
    entered = asyncio.Event()

    async def watch(stop, **kwargs):
        entered.set()
        await stop.wait()

    monkeypatch.setattr(worker, 'watch_refreshes', watch)
    task = asyncio.create_task(worker.main(SimpleNamespace(
        kind=['quest'], snapshot='current', estimate=False, entity_id=[], backfill=False, watch=True)))
    await asyncio.wait_for(entered.wait(), 1)
    callbacks[signal.SIGTERM]()
    await asyncio.wait_for(task, 1)
    assert remove.call_count == 2


@pytest.mark.asyncio
async def test_refresh_batch_runs_multiple_claims_concurrently(monkeypatch):
    claims = [SimpleNamespace(kind="character", entity_id=i, snapshot_key="current", generation=1) for i in (1, 2, 3)]
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=claims))
    active = 0
    peak = 0

    async def fake_refresh(factory, item):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1

    monkeypatch.setattr(worker, "refresh_claim", fake_refresh)
    assert await worker.refresh_batch(object(), limit=3, concurrency=2) == 3
    assert peak == 2
