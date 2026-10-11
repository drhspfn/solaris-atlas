import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import queue_control
from wuwa_story.ingestion.queue_control import clear_queue, drain_messages, run_queue


@pytest.mark.asyncio
async def test_cleanup_preserves_running_and_newer_messages():
    def message(run_id):
        return SimpleNamespace(body=json.dumps({"run_id": run_id}).encode(),
                               ack=AsyncMock(), nack=AsyncMock())
    cancelled, running, newer = (message(i) for i in (10, 11, 12))
    queue = SimpleNamespace(declaration_result=SimpleNamespace(message_count=3),
                            get=AsyncMock(side_effect=[cancelled, running, newer]))
    assert await drain_messages(queue, {10}) == 1
    cancelled.ack.assert_awaited_once()
    running.ack.assert_not_awaited()
    running.nack.assert_awaited_once_with(requeue=True)
    newer.nack.assert_awaited_once_with(requeue=True)


@pytest.mark.asyncio
async def test_cleanup_failed_messages_keeps_database_history():
    message = SimpleNamespace(body=b"invalid", ack=AsyncMock(), nack=AsyncMock())
    queue = SimpleNamespace(declaration_result=SimpleNamespace(message_count=1),
                            get=AsyncMock(return_value=message))
    assert await drain_messages(queue) == 1
    message.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_queue_rejected_before_connecting():
    with pytest.raises(ValueError):
        await clear_queue(AsyncMock(), "unrelated", "waiting")


def test_event_route_is_separate_from_regular_imports():
    assert run_queue("release_media", {"request": {"kind": "event_images"}}) == "wuwa.event-media.v1"
    assert run_queue("release_media", {"request": {"kind": "voices"}}) == "wuwa.release-media.v1"
    assert run_queue("release_media", {"queue": "wuwa.event-media.v1"}) == "wuwa.event-media.v1"


@pytest.mark.asyncio
async def test_cleanup_retry_removes_previously_cancelled_deliveries(monkeypatch):
    run = SimpleNamespace(id=10, status="cancelled", metadata_json={})
    event = SimpleNamespace(id=11, status="queued", metadata_json={"queue": "wuwa.event-media.v1"})
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(all=lambda: [(run, "release_media"), (event, "release_media")])
    message = SimpleNamespace(body=b'{"run_id": 10}', ack=AsyncMock(), nack=AsyncMock())
    queue = SimpleNamespace(declaration_result=SimpleNamespace(message_count=1),
                            get=AsyncMock(side_effect=[message, None]))
    channel = AsyncMock()
    channel.is_closed = False
    channel.declare_queue.return_value = queue
    connection = AsyncMock()
    connection.channel.return_value = channel
    monkeypatch.setattr(queue_control.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    result = await clear_queue(session, "wuwa.release-media.v1", "waiting")
    assert result == {"cancelled": 0, "removed": 1}
    assert event.status == "queued"
    message.ack.assert_awaited_once()
    session.commit.assert_awaited_once()
