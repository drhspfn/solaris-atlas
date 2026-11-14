from __future__ import annotations

import logging
import os

from prometheus_client import Counter, Gauge, Histogram, Info, start_http_server

logger = logging.getLogger(__name__)

WORKER_JOBS_TOTAL = Counter(
    "solaris_worker_jobs_total",
    "Total worker jobs processed",
    ["queue", "processor", "status"],
)
WORKER_JOB_DURATION_SECONDS = Histogram(
    "solaris_worker_job_duration_seconds",
    "Worker job execution duration in seconds",
    ["queue", "processor"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 300.0, 900.0, 3600.0),
)
WORKER_JOBS_ACTIVE = Gauge(
    "solaris_worker_jobs_active",
    "Currently executing worker jobs",
    ["queue", "processor"],
)
WORKER_INFO = Info("solaris_worker", "Worker runtime metadata")


def start_worker_metrics_server(
    *,
    service: str,
    pool: str | None = None,
    queues: list[str] | None = None,
) -> int | None:
    """Start the Prometheus metrics HTTP endpoint for this worker if enabled."""
    port_raw = os.getenv("WUWA_METRICS_PORT", "9101").strip()
    if not port_raw or port_raw == "0":
        return None
    port = int(port_raw)
    WORKER_INFO.info(
        {
            "service": service,
            "pool": pool or "",
            "queues": ",".join(queues or []),
        }
    )
    try:
        start_http_server(port)
        logger.info("worker.metrics_started port=%d service=%s", port, service)
        return port
    except OSError as exc:
        logger.warning(
            "worker.metrics_unavailable port=%d error=%s", port, exc)
        return None
