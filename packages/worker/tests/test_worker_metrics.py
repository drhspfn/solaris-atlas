from __future__ import annotations

import socket
import urllib.request

from wuwa_story_worker.metrics import (
    WORKER_JOBS_ACTIVE,
    WORKER_JOBS_TOTAL,
    start_worker_metrics_server,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_start_worker_metrics_server_disabled_when_zero(monkeypatch) -> None:
    monkeypatch.setenv("WUWA_METRICS_PORT", "0")
    assert start_worker_metrics_server(
        service="test-worker", queues=["snapshot_build"]) is None


def test_start_worker_metrics_server_exposes_worker_metrics(monkeypatch) -> None:
    port = _free_port()
    monkeypatch.setenv("WUWA_METRICS_PORT", str(port))

    WORKER_JOBS_TOTAL.labels(
        queue="snapshot_build", processor="snapshot.build", status="succeeded").inc()
    WORKER_JOBS_ACTIVE.labels(queue="snapshot_build",
                              processor="snapshot.build").set(1)

    started = start_worker_metrics_server(
        service="snapshot-worker",
        pool="snapshot",
        queues=["snapshot_build"],
    )
    assert started == port

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3) as resp:
        assert resp.status == 200
        body = resp.read().decode("utf-8")

    assert "solaris_worker_jobs_total" in body
    assert "solaris_worker_jobs_active" in body
    assert "solaris_worker_info" in body
