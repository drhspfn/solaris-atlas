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
    assert limits["release_media"] == limits["entity_media"] == 2
    assert limits["asset_extract"] == limits["asset_download"] == limits["event_media"] == 1


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
        channels[0].set_qos.assert_awaited_once_with(prefetch_count=2)
        channels[1].set_qos.assert_awaited_once_with(prefetch_count=1)
        assert queues[0].consume.call_args.args[0].__kwdefaults__["job_channel"] is channels[0]
        assert queues[1].consume.call_args.args[0].__kwdefaults__["job_channel"] is channels[1]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    connection.close.assert_awaited_once()
