import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import broker, cli, media_workspace
from wuwa_story_worker.queues import media_pool, queue_concurrency


@asynccontextmanager
async def consumer(monkeypatch, key, handler, **options):
    channel, queue, connection = AsyncMock(), AsyncMock(), AsyncMock()
    connection.channel.return_value = channel
    ready, stop = asyncio.Event(), asyncio.Event()
    queue.consume.side_effect = lambda *args, **kwargs: ready.set()
    monkeypatch.setattr(broker.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    declaration = AsyncMock(return_value=(channel.default_exchange, queue))
    monkeypatch.setattr(broker, "declare_queue", declaration)
    monkeypatch.setattr(broker, "claim_import", AsyncMock(return_value=True))
    monkeypatch.setattr(broker, "update_admin_run", AsyncMock())
    task = asyncio.create_task(broker.consume_jobs({key: handler}, stop=stop, **options))
    await asyncio.wait_for(ready.wait(), 2)
    try:
        yield queue.consume.call_args.args[0], channel, queue, stop, task
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)


def delivery(body=b'{"run_id":1,"kind":"voices","job_type":"media.release"}', **kwargs):
    return SimpleNamespace(body=body, headers={"db_retry_count": 2}, redelivered=False,
                           message_id="pinned-1", type="release_media", ack=AsyncMock(),
                           nack=AsyncMock(), **kwargs)


@pytest.mark.parametrize("payload,pool", [
    ({"kind": "prepare"}, "coordinator"),
    ({"kind": "event_catalog"}, "coordinator"),
    ({"kind": "images"}, "image"),
    ({"kind": "event_resources"}, "image"),
    ({"kind": "character_voices"}, "audio"),
    ({"kind": "voice_packages"}, "audio"),
    ({"kind": "cutscene_assets"}, "video"),
    ({"kind": "cutscene"}, "video"),
    ({"job_type": "media.entities", "request": {"kinds": ["voice"]}}, "audio"),
    ({"job_type": "media.entities", "request": {"kinds": ["image", "voice"]}}, "image"),
])
def test_media_pool_classification(payload, pool):
    assert media_pool(payload) == pool


@pytest.mark.parametrize("limit,valid", [(1, True), (4, True), (5, False)])
def test_snapshot_fetch_compile_parallelism_is_capped(monkeypatch, limit, valid):
    monkeypatch.setenv("WUWA_QUEUE_CONCURRENCY", f"snapshot_build={limit}")
    if valid:
        assert queue_concurrency()["snapshot_build"] == limit
    else:
        with pytest.raises(ValueError, match="cannot exceed 4"):
            queue_concurrency()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [True, False])
async def test_router_confirms_unchanged_payload_before_ack(monkeypatch, confirmed):
    handler = AsyncMock()
    async with consumer(monkeypatch, "release_media", handler, route_media=True) as (callback, channel, *_):
        message = delivery()
        async def publish(sent, **options):
            message.ack.assert_not_awaited()
            assert sent.body == message.body
            assert sent.message_id == message.message_id
            assert sent.type == message.type
            assert sent.headers == message.headers
            assert options == {"routing_key": "wuwa.pool-audio.v1", "mandatory": True}
            if not confirmed:
                raise OSError("Connection lost before confirmation")
        channel.default_exchange.publish.side_effect = publish
        await callback(message)
        handler.assert_not_awaited()
        broker.claim_import.assert_not_awaited()
        if confirmed:
            message.ack.assert_awaited_once()
        else:
            message.ack.assert_not_awaited()
            message.nack.assert_awaited_once_with(requeue=True)


@pytest.mark.asyncio
async def test_private_queue_deferral_keeps_source_type_and_processor(monkeypatch):
    async def execute(payload, handler):
        await handler(payload)
    monkeypatch.setattr(broker, "execute_media_job", execute)
    async with consumer(monkeypatch, "pool_audio", AsyncMock(side_effect=broker.JobDeferred("busy"))) as (callback, channel, *_):
        message = delivery()
        await callback(message)
        sent = channel.default_exchange.publish.call_args.args[0]
        assert sent.type == "release_media"
        assert sent.headers["db_retry_count"] == 2
        broker.update_admin_run.assert_awaited_once_with(1, "release_media", "waiting_dependency", error="busy")
        message.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_pool_never_exceeds_its_execution_and_prefetch_budget(monkeypatch):
    monkeypatch.setenv("WUWA_QUEUE_CONCURRENCY", "pool_image=2")
    started, release = asyncio.Event(), asyncio.Event()
    running = maximum = 0
    async def handler(payload):
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        if running == 2:
            started.set()
        await release.wait()
        running -= 1
    async def execute(payload, callback):
        await callback(payload)
    monkeypatch.setattr(broker, "execute_media_job", execute)
    async with consumer(monkeypatch, "pool_image", handler) as (callback, channel, *_):
        channel.set_qos.assert_awaited_once_with(prefetch_count=2)
        deliveries = [asyncio.create_task(callback(delivery())) for _ in range(5)]
        await asyncio.wait_for(started.wait(), 1)
        assert maximum == 2
        release.set()
        await asyncio.gather(*deliveries)
        assert maximum == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_shutdown_cancels_consumers_then_drains_or_requeues(monkeypatch, timeout):
    monkeypatch.setenv("WUWA_SHUTDOWN_TIMEOUT_SECONDS", "0.01" if timeout else "1")
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(payload):
        entered.set()
        await release.wait()
    async with consumer(monkeypatch, "snapshot_build", handler) as (callback, _, queue, stop, runner):
        message = delivery()
        delivery_task = asyncio.create_task(callback(message))
        await asyncio.wait_for(entered.wait(), 1)
        stop.set()
        await asyncio.sleep(0)
        queue.cancel.assert_awaited_once()
        if not timeout:
            assert not runner.done()
            release.set()
        await asyncio.wait_for(runner, 1)
        if timeout:
            with pytest.raises(asyncio.CancelledError):
                await delivery_task
            message.nack.assert_awaited_once_with(requeue=True)
            message.ack.assert_not_awaited()
        else:
            await delivery_task
            message.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_delivery_skips_handler_and_cleans_workspace(monkeypatch):
    cleanup = AsyncMock()
    monkeypatch.setattr(broker, "cleanup_skipped_media", cleanup)
    async with consumer(monkeypatch, "pool_audio", AsyncMock()) as (callback, *_):
        broker.claim_import.return_value = False
        message = delivery()
        await callback(message)
        cleanup.assert_awaited_once_with({"run_id": 1, "kind": "voices", "job_type": "media.release"})
        message.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_lease_survives_cancellation_until_writes_finish(monkeypatch):
    events = []
    entered, release = asyncio.Event(), asyncio.Event()
    @asynccontextmanager
    async def lease(workspace_id):
        assert workspace_id == 42
        events.append("lease")
        try:
            yield
        finally:
            events.append("release")
    async def handler(payload):
        entered.set()
        await release.wait()
        events.append("writes_finished")
    async def cleanup(workspace_id):
        events.append("cleanup")
    monkeypatch.setattr(media_workspace, "run_workspace_id", AsyncMock(return_value=42))
    monkeypatch.setattr(media_workspace, "workspace_lease", lease)
    monkeypatch.setattr(media_workspace, "cleanup_workspace", cleanup)
    task = asyncio.create_task(broker.execute_media_job({"run_id": 1}, handler))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    assert events == ["lease"]
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["lease", "writes_finished", "release", "cleanup"]


@pytest.mark.asyncio
async def test_recovery_scans_only_owned_numeric_directories(monkeypatch, tmp_path):
    root = tmp_path / "patch-workspaces"
    for name in ("42", ".locks", "0", "9223372036854775808", "other"):
        (root / name).mkdir(parents=True)
    import wuwa_story_worker.tooling as tooling
    monkeypatch.setattr(tooling, "asset_workspace", lambda: tmp_path)
    stop = asyncio.Event()
    async def cleanup(workspace_id):
        assert workspace_id == 42
        stop.set()
    mock = AsyncMock(side_effect=cleanup)
    monkeypatch.setattr(media_workspace, "cleanup_workspace", mock)
    await asyncio.wait_for(broker.recover_media_workspaces(stop), 1)
    mock.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_pool_cli_dispatch_and_shared_engine_pool_budget(monkeypatch):
    consume = AsyncMock()
    entity, release = AsyncMock(), AsyncMock()
    monkeypatch.setattr(cli, "consume_jobs", consume)
    monkeypatch.setattr(cli, "process_entity_media", entity)
    monkeypatch.setattr(cli, "process_release_media", release)
    monkeypatch.setenv("WUWA_WORKER_DB_POOL_SIZE", "4")
    monkeypatch.setenv("WUWA_WORKER_DB_MAX_OVERFLOW", "4")
    await cli._run(pool="audio")
    selected = consume.call_args.args[0]
    assert set(selected) == {"pool_audio"}
    assert consume.call_args.kwargs["route_media"] is False
    await selected["pool_audio"]({"job_type": "media.entities"})
    entity.assert_awaited_once()
    release.assert_not_awaited()
