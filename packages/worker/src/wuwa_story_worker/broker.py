"""Durable RabbitMQ publishing and consumption helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from wuwa_story_worker.queues import QUEUES, queue_concurrency

logger = logging.getLogger(__name__)
EXCHANGE_NAME = "wuwa.jobs.v1"
FAILED_EXCHANGE_NAME = "wuwa.jobs.failed.v1"
JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


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
    connection = await aio_pika.connect_robust(broker_url())
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=sum(limits[key] for key in handlers))
        for key, handler in handlers.items():
            _, queue = await declare_queue(channel, key)
            semaphore = asyncio.Semaphore(limits[key])

            async def process(
                message: AbstractIncomingMessage,
                *,
                job_key: str = key,
                job_handler: JobHandler = handler,
                limiter: asyncio.Semaphore = semaphore,
            ) -> None:
                async with limiter:
                    try:
                        payload = json.loads(message.body)
                        if not isinstance(payload, dict):
                            raise ValueError("Job payload must be a JSON object")
                        await job_handler(payload)
                    except Exception:
                        logger.exception(
                            "Worker job failed (%s, id=%s); moving it to the failed queue",
                            job_key,
                            message.message_id,
                        )
                        await message.nack(requeue=False)
                    else:
                        await message.ack()

            await queue.consume(process, no_ack=False)
            logger.info("Consuming %s with concurrency=%d", QUEUES[key].name, limits[key])
        await asyncio.Future()
    finally:
        await connection.close()
