from __future__ import annotations

import json
import logging
import os
import socket
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOG_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "wuwa_log_context", default=None)

_STANDARD_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def get_log_context() -> dict[str, Any]:
    """Return a copy of the current async/thread log context."""
    return dict(_LOG_CONTEXT.get() or {})


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Bind structured fields into the active logging context until exit."""
    current = _LOG_CONTEXT.get() or {}
    merged = {**current, **{k: v for k, v in fields.items() if v is not None}}
    token = _LOG_CONTEXT.set(merged)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def _extract_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
            continue
        extras[key] = value
    return extras


class CompactConsoleFormatter(logging.Formatter):
    """Concise single-line formatter for stdout / docker logs."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(
            record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        ctx = _LOG_CONTEXT.get() or {}
        tags: list[str] = []
        if req_id := ctx.get("request_id"):
            tags.append(f"req={str(req_id)[:8]}")
        if job_id := ctx.get("job_id"):
            tags.append(f"job={job_id}")
        if run_id := ctx.get("run_id"):
            tags.append(f"run={run_id}")
        tag_str = f" [{' '.join(tags)}]" if tags else ""
        msg = record.getMessage()
        base = f"{ts} {record.levelname} {record.name}{tag_str} {msg}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


class JsonLineFormatter(logging.Formatter):
    """Full structured JSONL formatter for Loki / Grafana ingestion."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ctx = _LOG_CONTEXT.get() or {}
        extras = _extract_extras(record)
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": ctx.get("service") or self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Promote key correlation/filtering fields to top-level JSON keys.
        merged_context = {**ctx, **extras}
        for key in (
            "request_id",
            "method",
            "path",
            "route",
            "status_code",
            "duration_ms",
            "client_ip",
            "user_id",
            "job_id",
            "queue",
            "processor",
            "run_id",
        ):
            if key in merged_context and merged_context[key] is not None:
                payload[key] = merged_context.pop(key)
        src_file = merged_context.pop("source_file", None)
        src_line = merged_context.pop("source_line", None)
        src_func = merged_context.pop("source_function", None)
        if merged_context:
            payload["context"] = merged_context
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_val, exc_tb = record.exc_info
            tb_frames = traceback.extract_tb(exc_tb)
            last_frame = tb_frames[-1] if tb_frames else None
            payload["exception"] = {
                "type": exc_type.__name__,
                "message": str(exc_val),
                "traceback": "".join(traceback.format_exception(exc_type, exc_val, exc_tb)),
            }
            if last_frame is not None:
                payload["source"] = {
                    "file": Path(last_frame.filename).name,
                    "line": last_frame.lineno,
                    "function": last_frame.name,
                }
        elif record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        if "source" not in payload:
            if src_file:
                payload["source"] = {
                    "file": src_file,
                    "line": src_line,
                    "function": src_func,
                }
            else:
                payload["source"] = {
                    "file": record.filename,
                    "line": record.lineno,
                    "function": record.funcName,
                }
        return json.dumps(payload, ensure_ascii=False, default=str)


def _resolve_json_log_path(service: str) -> Path | None:
    explicit = os.getenv("WUWA_LOG_JSON_PATH", "").strip()
    if explicit:
        return Path(explicit)
    directory = os.getenv("WUWA_LOG_JSON_DIR", "").strip()
    if directory:
        safe_service = service.replace("/", "_").replace(" ", "_")
        host = socket.gethostname().replace("/", "_")
        return Path(directory) / f"{safe_service}-{host}.jsonl"
    return None


def configure_logging(level: str, *, service: str | None = None) -> None:
    """Configure compact console logging and optional structured JSONL file logging."""
    resolved_service = service or os.getenv(
        "WUWA_LOG_SERVICE", "solaris-api").strip() or "solaris-api"
    root = logging.getLogger()
    root.setLevel(level.upper())

    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(CompactConsoleFormatter())
    root.addHandler(console)

    json_path = _resolve_json_log_path(resolved_service)
    if json_path is not None:
        try:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            max_bytes = int(
                os.getenv("WUWA_LOG_JSON_MAX_BYTES", str(50 * 1024 * 1024)))
            backups = int(os.getenv("WUWA_LOG_JSON_BACKUPS", "3"))
            file_handler = RotatingFileHandler(
                json_path,
                maxBytes=max_bytes,
                backupCount=backups,
                encoding="utf-8",
            )
            file_handler.setFormatter(JsonLineFormatter(resolved_service))
            root.addHandler(file_handler)
        except OSError as exc:
            root.warning("Could not open JSON log file %s: %s", json_path, exc)

    # Keep noisy AMQP/HTTP client heartbeats from flooding info logs.
    for noisy in ("aiormq", "aio_pika", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
