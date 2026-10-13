import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import broker
from wuwa_story_worker.queues import queue_concurrency


@pytest.mark.asyncio
@pytest.mark.parametrize("status,redelivered,expected", [
    ("cancelled", True, False), ("completed", False, False),
    ("running", False, False), ("running", True, True),
    ("queued", False, True), ("waiting_dependency", False, True),
])
async def test_import_claim_respects_cancellation_and_duplicate_delivery(monkeypatch, status, redelivered, expected):
    run = SimpleNamespace(status=status)
    session = AsyncMock()
    session.scalar.return_value = run
    session.__aenter__.return_value = session
    monkeypatch.setattr(broker, "SessionFactory", lambda: session)
    assert await broker.claim_import({"run_id": 1}, redelivered=redelivered) is expected
    if expected:
        assert run.status == "running"
        session.commit.assert_awaited_once()
    else:
        session.commit.assert_not_awaited()


def test_media_parallelism_is_bounded_and_heavy_extractors_remain_serial(monkeypatch):
    monkeypatch.delenv("WUWA_QUEUE_CONCURRENCY", raising=False)
    limits = queue_concurrency()
    assert limits["release_media"] == 4
    assert limits["entity_media"] == 3
    assert limits["event_media"] == 2
    assert limits["asset_extract"] == limits["asset_download"] == 1


@pytest.mark.asyncio
async def test_consumers_use_independent_delivery_limits(monkeypatch):
    channels = [AsyncMock(), AsyncMock()]
    connection = AsyncMock()
    connection.channel.side_effect = channels
    monkeypatch.setattr(broker.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    monkeypatch.delenv("WUWA_QUEUE_CONCURRENCY", raising=False)
    ready = asyncio.Event()
    queues = [AsyncMock(), AsyncMock()]
    queues[1].consume.side_effect = lambda *args, **kwargs: ready.set()
    monkeypatch.setattr(broker, "declare_queue", AsyncMock(side_effect=[(None, q) for q in queues]))
    task = asyncio.create_task(broker.consume_jobs({"release_media": AsyncMock(), "event_media": AsyncMock()}))
    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
        channels[0].set_qos.assert_awaited_once_with(prefetch_count=4)
        channels[1].set_qos.assert_awaited_once_with(prefetch_count=2)
        assert queues[0].consume.call_args.args[0].__kwdefaults__["job_channel"] is channels[0]
        assert queues[1].consume.call_args.args[0].__kwdefaults__["job_channel"] is channels[1]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_job_lock_defers_without_waiting_and_discards_connection():
    connection = AsyncMock()
    connection.scalar.return_value = False
    with pytest.raises(broker.JobDeferred):
        async with broker.job_lock(connection, 702, 96):
            pytest.fail("A busy job must not execute")
    assert "pg_try_advisory_lock" in str(connection.scalar.call_args.args[0])
    connection.invalidate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, ValueError("failed"), asyncio.CancelledError()])
async def test_job_lock_cannot_leak_after_completion_failure_or_cancellation(error):
    connection = AsyncMock()
    connection.scalar.return_value = True
    async def execute():
        async with broker.job_lock(connection, -182):
            connection.commit.assert_awaited_once()
            if error is not None:
                raise error
    if error is None:
        await execute()
    else:
        with pytest.raises(type(error)):
            await execute()
    connection.invalidate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 3])
async def test_database_contention_has_bounded_durable_retries(monkeypatch, retries):
    class Deadlock(Exception):
        sqlstate = "40P01"
    handler = AsyncMock(side_effect=broker.DBAPIError(None, None, Deadlock()))
    channel, queue, connection = AsyncMock(), AsyncMock(), AsyncMock()
    connection.channel.return_value = channel
    ready = asyncio.Event()
    queue.consume.side_effect = lambda *args, **kwargs: ready.set()
    monkeypatch.setattr(broker.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    monkeypatch.setattr(broker, "declare_queue", AsyncMock(return_value=(None, queue)))
    monkeypatch.setattr(broker, "claim_import", AsyncMock(return_value=True))
    update = AsyncMock()
    monkeypatch.setattr(broker, "update_admin_run", update)
    task = asyncio.create_task(broker.consume_jobs({"entity_media": handler}))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        callback = queue.consume.call_args.args[0]
        message = SimpleNamespace(body=b'{"run_id":182}', headers={"db_retry_count": retries},
                                  redelivered=False, message_id="test", type="entity_media",
                                  ack=AsyncMock(), nack=AsyncMock())
        await callback(message)
        if retries < 3:
            sent = channel.default_exchange.publish.call_args.args[0]
            assert sent.headers["db_retry_count"] == 1
            assert sent.body == message.body
            message.ack.assert_awaited_once()
            message.nack.assert_not_awaited()
            update.assert_awaited_once()
        else:
            message.nack.assert_awaited_once_with(requeue=False)
            channel.default_exchange.publish.assert_not_awaited()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_deferred_dependency_releases_slot_for_next_task(monkeypatch):
    monkeypatch.setenv("WUWA_QUEUE_CONCURRENCY", "release_media=1")
    channel, queue, connection = AsyncMock(), AsyncMock(), AsyncMock()
    connection.channel.return_value = channel
    ready = asyncio.Event()
    queue.consume.side_effect = lambda *args, **kwargs: ready.set()
    monkeypatch.setattr(broker.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    monkeypatch.setattr(broker, "declare_queue", AsyncMock(return_value=(None, queue)))
    monkeypatch.setattr(broker, "claim_import", AsyncMock(return_value=True))
    handler = AsyncMock(side_effect=[broker.JobDeferred("Dependency not ready"), None])
    task = asyncio.create_task(broker.consume_jobs({"release_media": handler}))
    try:
        await asyncio.wait_for(ready.wait(), 2)
        callback = queue.consume.call_args.args[0]
        def message(run):
            return SimpleNamespace(body=f'{{"run_id":{run}}}'.encode(), headers={},
                                   redelivered=False, message_id=str(run), type="release_media",
                                   ack=AsyncMock(), nack=AsyncMock())
        waiting, next_job = message(1), message(2)
        await asyncio.wait_for(asyncio.gather(callback(waiting), callback(next_job)), 2)
        waiting.ack.assert_awaited_once()
        next_job.ack.assert_awaited_once()
        assert handler.await_count == 2
        sent = channel.default_exchange.publish.call_args.args[0]
        assert sent.body == waiting.body
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
