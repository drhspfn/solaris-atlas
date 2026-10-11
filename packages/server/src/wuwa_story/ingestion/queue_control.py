"""Cancel waiting imports and remove their broker messages, retaining run history."""

import json
import logging
from datetime import UTC, datetime

import aio_pika
from sqlalchemy import select

from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.ops import ProcessingRun, Processor

logger = logging.getLogger(__name__)
QUEUE_PROCESSORS = {
    "wuwa.release-media.v1": {"release_media"},
    "wuwa.event-media.v1": {"release_media"},
    "wuwa.entity-media.v1": {"entity_media"},
    "wuwa.asset-download.v1": {"asset_download"},
    "wuwa.asset-extract.v1": {"asset_extract", "map_build"},
    "wuwa.snapshot-build.v1": {"snapshot_import"},
}
WAITING = {"queued", "pending", "waiting_dependency", "enqueue_failed"}


def run_queue(processor, metadata):
    if processor == "release_media":
        return metadata.get("queue") or (
            "wuwa.event-media.v1" if metadata.get("request", {}).get("kind") == "event_images"
            else "wuwa.release-media.v1"
        )
    return next((name for name, processors in QUEUE_PROCESSORS.items()
                 if processor in processors), None)


async def drain_messages(queue, cancelled_ids=None, limit=5000):
    """Inspect only the initial backlog; preserve newer and executing messages."""
    retained = []
    removed = 0
    try:
        count = min(queue.declaration_result.message_count or 0, limit)
        for _ in range(count):
            message = await queue.get(no_ack=False, fail=False)
            if message is None:
                break
            try:
                payload = json.loads(message.body)
                run_id = payload.get("run_id") if isinstance(payload, dict) else None
            except (ValueError, TypeError):
                run_id = None
            if cancelled_ids is None or run_id is None or run_id in cancelled_ids:
                await message.ack()
                removed += 1
            else:
                retained.append(message)
    finally:
        for message in retained:
            await message.nack(requeue=True)
    return removed


async def clear_queue(session, name, scope):
    if name not in QUEUE_PROCESSORS or scope not in {"waiting", "failed"}:
        raise ValueError("Unknown import queue or cleanup scope")
    connection = await aio_pika.connect_robust(
        get_settings().rabbitmq_url.get_secret_value(), timeout=5
    )
    cancelled = set()
    newly_cancelled = 0
    removed = 0
    try:
        if scope == "waiting":
            rows = (await session.execute(
                select(ProcessingRun, Processor.key).join(Processor)
                .where(ProcessingRun.status.in_(WAITING | {"cancelled"}),
                       Processor.key.in_(QUEUE_PROCESSORS[name]))
                .with_for_update(of=ProcessingRun)
            )).all()
            for run, processor in rows:
                if run_queue(processor, run.metadata_json or {}) != name:
                    continue
                if run.status != "cancelled":
                    run.status = "cancelled"
                    run.finished_at = datetime.now(UTC)
                    run.error = "Waiting task cancelled by an administrator"
                    newly_cancelled += 1
                cancelled.add(run.id)
            await session.commit()
        suffixes = (".failed",) if scope == "failed" else ("", ".waiting")
        for suffix in suffixes:
            # Separate channels: a passive declaration of a missing queue closes its channel.
            probe = await connection.channel()
            try:
                queue = await probe.declare_queue(name + suffix, passive=True)
                removed += await drain_messages(queue, cancelled if scope == "waiting" else None)
            except aio_pika.exceptions.ChannelNotFoundEntity:
                pass
            finally:
                if not probe.is_closed:
                    await probe.close()
        logger.info("queue.cleanup queue=%s scope=%s cancelled=%s removed=%s",
                    name, scope, newly_cancelled, removed)
        return {"cancelled": newly_cancelled, "removed": removed}
    finally:
        await connection.close()
