"""Confirmed durable publication and inspectable media processing runs."""

import hashlib
import json

import aio_pika
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.ingestion.entity_media import MediaRequest, entity_media_targets

MEDIA_QUEUE = "wuwa.entity-media.v1"


async def publish_media_job(payload: dict, job_id: str, queue_name: str = MEDIA_QUEUE) -> None:
    connection = await aio_pika.connect_robust(get_settings().rabbitmq_url.get_secret_value())
    try:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.declare_exchange(
            "wuwa.jobs.v1", aio_pika.ExchangeType.DIRECT, durable=True
        )
        failed = await channel.declare_exchange(
            "wuwa.jobs.failed.v1", aio_pika.ExchangeType.DIRECT, durable=True
        )
        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": failed.name,
                "x-dead-letter-routing-key": queue_name,
            },
        )
        await queue.bind(exchange, routing_key=queue_name)
        dead = await channel.declare_queue(queue_name + ".failed", durable=True)
        await dead.bind(failed, routing_key=queue_name)
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(payload).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=job_id,
            ),
            routing_key=queue_name,
            mandatory=True,
        )
    finally:
        await connection.close()


async def enqueue_entity_media(session, request: MediaRequest) -> ProcessingRun:
    targets = await entity_media_targets(session, request)
    payload = {
        "schema_version": 1,
        "job_type": "media.entities",
        "request": request.model_dump(),
        "targets": targets,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:id)"), {"id": int.from_bytes(digest[:7], "big")}
    )
    await session.execute(
        insert(Processor)
        .values(key="entity_media", version="1")
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor = await session.scalar(select(Processor.id).where(Processor.key == "entity_media"))
    run = await session.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.processor_id == processor, ProcessingRun.input_hash == digest)
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    if run and run.status in ("completed", "partial", "running"):
        return run
    if run is None:
        run = ProcessingRun(processor_id=processor, input_hash=digest)
        session.add(run)
        await session.flush()
    run.status = "queued"
    run.error = None
    payload["run_id"] = run.id
    run.metadata_json = {"payload": payload}
    await session.commit()
    try:
        await publish_media_job(payload, digest.hex())
    except Exception:
        run.status = "enqueue_failed"
        run.error = "Broker did not confirm publication; repeat the same request to retry"
        await session.commit()
        raise
    return run
