"""Durable RabbitMQ publishing and consumption helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.session import SessionFactory

from wuwa_story_worker.job_tracking import update_admin_run
from wuwa_story_worker.notifications import notify_job_result
from wuwa_story_worker.queues import QUEUES, queue_concurrency

logger = logging.getLogger(__name__)
EXCHANGE_NAME = "wuwa.jobs.v1"
FAILED_EXCHANGE_NAME = "wuwa.jobs.failed.v1"
JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def claim_import(payload, *, redelivered=False):
    """Serialize cancellation with execution; cancelled deliveries are acknowledged."""
    run_id = payload.get("run_id")
    if run_id is None:
        return True
    async with SessionFactory() as session:
        run = await session.scalar(select(ProcessingRun).where(ProcessingRun.id == run_id)
                                   .with_for_update())
        if run is None or run.status in {"cancelled", "completed", "partial"}:
            return False
        if run.status == "running" and not redelivered:
            return False
        run.status = "running"
        await session.commit()
        return True


class JobDeferred(Exception):
    """A prerequisite is still queued/running; retry through a durable delay queue."""


@asynccontextmanager
async def job_lock(connection, *keys: int):
    """Busy jobs yield their slot; close the DB session so locks cannot leak into the pool."""
    params = {f"key{i}": key for i, key in enumerate(keys)}
    args = ", ".join(f":{name}" for name in params)
    try:
        acquired = await connection.scalar(text(f"SELECT pg_try_advisory_lock({args})"), params)
        await connection.commit()
        if not acquired:
            raise JobDeferred("Another worker is processing this task")
        yield
    finally:
        # Session-level advisory locks survive COMMIT/ROLLBACK. Never return this
        # physical connection to the pool, including cancellation/error paths.
        await connection.invalidate()


async def defer_job(channel, key, message, *, retry_count=None) -> None:
    name = QUEUES[key].name
    queue = await channel.declare_queue(name + ".waiting", durable=True, arguments={
        "x-message-ttl": 60000, "x-dead-letter-exchange": EXCHANGE_NAME,
        "x-dead-letter-routing-key": name})
    headers = dict(message.headers or {})
    if retry_count is not None:
        headers["db_retry_count"] = retry_count
    await channel.default_exchange.publish(aio_pika.Message(
        body=message.body, content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT, message_id=message.message_id,
        headers=headers, type=message.type),
        routing_key=queue.name, mandatory=True)
    await message.ack()


def broker_url() -> str:
    return os.getenv("RABBITMQ_URL", "amqp://wuwa:wuwa@localhost:5672/")


async def declare_queue(channel: aio_pika.abc.AbstractChannel, key: str):
    spec = QUEUES[key]
    exchange = await channel.declare_exchange(EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True)
    failed_exchange = await channel.declare_exchange(
        FAILED_EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )
    queue = await channel.declare_queue(
        spec.name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": FAILED_EXCHANGE_NAME,
            "x-dead-letter-routing-key": spec.name,
        },
    )
    await queue.bind(exchange, routing_key=spec.name)
    failed = await channel.declare_queue(f"{spec.name}.failed", durable=True)
    await failed.bind(failed_exchange, routing_key=spec.name)
    return exchange, queue


async def publish_job(key: str, payload: dict[str, Any], message_id: str) -> None:
    connection = await aio_pika.connect_robust(broker_url())
    try:
        channel = await connection.channel(publisher_confirms=True)
        exchange, _ = await declare_queue(channel, key)
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message_id,
                type=key,
            ),
            routing_key=QUEUES[key].name,
            mandatory=True,
        )
    finally:
        await connection.close()


async def replay_failed_jobs(key: str, limit: int) -> int:
    """Move up to ``limit`` dead-lettered messages back onto their work queue."""
    if limit < 1:
        raise ValueError("Replay limit must be positive")
    connection = await aio_pika.connect_robust(broker_url())
    replayed = 0
    try:
        channel = await connection.channel(publisher_confirms=True)
        exchange, _ = await declare_queue(channel, key)
        failed = await channel.get_queue(f"{QUEUES[key].name}.failed", ensure=True)
        while replayed < limit:
            message = await failed.get(no_ack=False, fail=False)
            if message is None:
                break
            await exchange.publish(
                aio_pika.Message(
                    body=message.body,
                    content_type=message.content_type or "application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=message.message_id,
                    type=message.type,
                ),
                routing_key=QUEUES[key].name,
                mandatory=True,
            )
            await message.ack()
            replayed += 1
    finally:
        await connection.close()
    return replayed


async def consume_jobs(handlers: dict[str, JobHandler]) -> None:
    if not handlers:
        raise ValueError("At least one queue handler must be registered")
    limits = queue_concurrency()
    connection = await aio_pika.connect_robust(broker_url(), client_properties={
        "connection_name": f"worker:{socket.gethostname()}:{os.getpid()}"})
    try:
        for key, handler in handlers.items():
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=limits[key])
            _, queue = await declare_queue(channel, key)
            semaphore = asyncio.Semaphore(limits[key])

            async def process(
                message: AbstractIncomingMessage,
                *,
                job_key: str = key,
                job_handler: JobHandler = handler,
                limiter: asyncio.Semaphore = semaphore,
                job_channel=channel,
            ) -> None:
                async with limiter:
                    payload = {}
                    try:
                        payload = json.loads(message.body)
                        if not isinstance(payload, dict):
                            raise ValueError("Job payload must be a JSON object")
                        if job_key in {"release_media", "event_media", "entity_media", "asset_download",
                                       "asset_extract", "snapshot_build"}:
                            if not await claim_import(payload, redelivered=message.redelivered):
                                await message.ack()
                                return
                        await job_handler(payload)
                    except JobDeferred:
                        try:
                            await defer_job(job_channel, job_key, message)
                        except Exception:
                            logger.exception("Could not defer %s", message.message_id)
                            await message.nack(requeue=True)
                    except DBAPIError as error:
                        retries = int((message.headers or {}).get("db_retry_count", 0))
                        if getattr(error.orig, "sqlstate", None) in {"40P01", "40001"} and retries < 3:
                            try:
                                await update_admin_run(payload["run_id"], job_key, "waiting_dependency",
                                                       error=f"Database contention; retry {retries + 1}/3")
                                await defer_job(job_channel, job_key, message, retry_count=retries + 1)
                                logger.warning("Deferred %s run=%s after database contention (retry %s/3)",
                                               job_key, payload["run_id"], retries + 1)
                            except Exception:
                                logger.exception("Could not defer database retry")
                                await message.nack(requeue=True)
                        else:
                            logger.exception("Database job failure exhausted retries (%s)", job_key)
                            await notify_job_result(payload.get("run_id"), job_key, str(error))
                            await message.nack(requeue=False)
                    except Exception as error:
                        logger.exception(
                            "Worker job failed (%s, id=%s); moving it to the failed queue",
                            job_key,
                            message.message_id,
                        )
                        await notify_job_result(payload.get("run_id") if isinstance(payload, dict) else None,
                                                job_key, str(error))
                        await message.nack(requeue=False)
                    else:
                        await notify_job_result(payload.get("run_id"), job_key)
                        await message.ack()

            await queue.consume(process, no_ack=False)
            logger.info("Consuming %s with concurrency=%d", QUEUES[key].name, limits[key])
        await asyncio.Future()
    finally:
        await connection.close()
