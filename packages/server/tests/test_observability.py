from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wuwa_story.api.observability import ObservabilityMiddleware, metrics_router
from wuwa_story.config.logging import (
    CompactConsoleFormatter,
    JsonLineFormatter,
    configure_logging,
    log_context,
)


def test_compact_and_json_formatters_include_context_and_exception() -> None:
    record_stream = StringIO()
    console_handler = logging.StreamHandler(record_stream)
    console_handler.setFormatter(CompactConsoleFormatter())

    json_formatter = JsonLineFormatter(service="api")

    logger = logging.getLogger("wuwa_story.test_obs")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    captured_json: list[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_json.append(json_formatter.format(record))

    logger.addHandler(_CaptureHandler())

    with log_context(request_id="req-1234567890", job_id="job-42", run_id=7):
        logger.info("step completed", extra={"custom_flag": True, "duration_ms": 12.5})
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("step failed")

    console_lines = record_stream.getvalue().strip().splitlines()
    assert "[req=req-1234 job=job-42 run=7]" in console_lines[0]
    assert "step completed" in console_lines[0]

    first_payload = json.loads(captured_json[0])
    assert first_payload["service"] == "api"
    assert first_payload["request_id"] == "req-1234567890"
    assert first_payload["job_id"] == "job-42"
    assert first_payload["run_id"] == 7
    assert first_payload["duration_ms"] == 12.5
    assert first_payload["context"]["custom_flag"] is True

    error_payload = json.loads(captured_json[1])
    assert error_payload["level"] == "ERROR"
    assert error_payload["exception"]["type"] == "ValueError"
    assert error_payload["exception"]["message"] == "boom"
    assert "ValueError: boom" in error_payload["exception"]["traceback"]


def test_configure_logging_writes_jsonl_file(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "api.jsonl"
    monkeypatch.setenv("WUWA_LOG_JSON_PATH", str(log_path))
    configure_logging("INFO", service="api")

    test_logger = logging.getLogger("wuwa_story.test_file")
    with log_context(request_id="abc-123"):
        test_logger.info("hello jsonl")

    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert content
    payload = json.loads(content[-1])
    assert payload["service"] == "api"
    assert payload["request_id"] == "abc-123"
    assert payload["message"] == "hello jsonl"


def test_observability_middleware_and_metrics_endpoint() -> None:
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)
    app.include_router(metrics_router)

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/ping", headers={"X-Request-ID": "trace-abc"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "trace-abc"

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    body = metrics_resp.text
    assert "solaris_http_requests_total" in body
    assert 'route="/ping"' in body
    assert "solaris_app_uptime_seconds" in body


def test_fast_200_http_requests_are_not_logged_by_default(caplog) -> None:
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/fast")
    def fast() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/broken")
    def broken() -> dict[str, bool]:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="fail")

    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="wuwa_story.http"):
        assert client.get("/fast").status_code == 200
        assert not [r for r in caplog.records if "http.request GET /fast" in r.message]

        assert client.get("/broken").status_code == 500
        assert any("http.request GET /broken" in r.message for r in caplog.records)
