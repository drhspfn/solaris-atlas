"""RabbitMQ queue definitions and per-queue concurrency settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueueSpec:
    key: str
    name: str
    default_concurrency: int


QUEUES = {
    "release_media": QueueSpec(key="release_media", name="wuwa.release-media.v1", default_concurrency=2),
    "event_media": QueueSpec(key="event_media", name="wuwa.event-media.v1", default_concurrency=1),
    "cutscene_vision": QueueSpec(key="cutscene_vision", name="wuwa.cutscene-vision.v1", default_concurrency=1),
    "story_agent": QueueSpec(key="story_agent", name="wuwa.story-agent.v1", default_concurrency=1),
    "entity_media": QueueSpec(key="entity_media", name="wuwa.entity-media.v1", default_concurrency=2),
    "asset_extract": QueueSpec(
        key="asset_extract",
        name="wuwa.asset-extract.v1",
        default_concurrency=1,
    ),
    "asset_download": QueueSpec(
        key="asset_download",
        name="wuwa.asset-download.v1",
        default_concurrency=1,
    ),
    "snapshot_build": QueueSpec(
        key="snapshot_build",
        name="wuwa.snapshot-build.v1",
        default_concurrency=1,
    ),
}


def queue_concurrency() -> dict[str, int]:
    """Parse WUWA_QUEUE_CONCURRENCY, e.g. ``snapshot_build=1``."""
    values = {key: queue.default_concurrency for key, queue in QUEUES.items()}
    configured = os.getenv("WUWA_QUEUE_CONCURRENCY", "")
    for entry in filter(None, (part.strip() for part in configured.split(","))):
        try:
            key, raw_limit = (part.strip() for part in entry.split("=", 1))
            limit = int(raw_limit)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "WUWA_QUEUE_CONCURRENCY must contain comma-separated queue=positive_integer pairs"
            ) from exc
        if key not in QUEUES:
            raise ValueError(f"Unknown worker queue concurrency key: {key!r}")
        if limit < 1:
            raise ValueError(f"Concurrency for queue {key!r} must be positive")
        values[key] = limit
    return values
