from __future__ import annotations

import inspect
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.middleware.base import BaseHTTPMiddleware

from wuwa_story.config.logging import log_context

logger = logging.getLogger("wuwa_story.http")

_START_TIME = time.monotonic()

HTTP_REQUESTS_TOTAL = Counter(
    "solaris_http_requests_total",
    "Total HTTP requests handled by the API",
    ["method", "route", "status_code"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "solaris_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25,
             0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "solaris_http_requests_in_progress",
    "Number of HTTP requests currently in progress",
    ["method", "route"],
    multiprocess_mode="livesum",
)
APP_UPTIME_SECONDS = Gauge(
    "solaris_app_uptime_seconds",
    "API process uptime in seconds",
    multiprocess_mode="max",
)

metrics_router = APIRouter(include_in_schema=False)


@metrics_router.get("/metrics")
async def prometheus_metrics() -> Response:
    APP_UPTIME_SECONDS.set(max(time.monotonic() - _START_TIME, 0.0))
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        payload = generate_latest(registry)
    else:
        payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    raw = request.url.path
    if raw in ("/health", "/metrics", "/"):
        return raw
    return "unmatched"


def _client_ip(request: Request) -> str | None:
    forwarded = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("true-client-ip")
        or request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for")
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _endpoint_source(request: Request) -> dict[str, object]:
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return {}
    try:
        fn = inspect.unwrap(endpoint)
        code = getattr(fn, "__code__", None)
        if code is None:
            return {}
        return {
            "source_file": Path(code.co_filename).name,
            "source_line": code.co_firstlineno,
            "source_function": getattr(fn, "__qualname__", fn.__name__),
        }
    except Exception:
        return {}


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Attach request IDs, structured log context, and Prometheus HTTP metrics."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(
            "x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        method = request.method
        path = request.url.path
        client_ip = _client_ip(request)
        started = time.monotonic()

        with log_context(
            request_id=request_id,
            method=method,
            path=path,
            client_ip=client_ip,
        ):
            in_progress_route = _route_label(request)
            HTTP_REQUESTS_IN_PROGRESS.labels(
                method=method, route=in_progress_route).inc()
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers.setdefault("X-Request-ID", request_id)
                return response
            except Exception:
                route = _route_label(request)
                duration_ms = round((time.monotonic() - started) * 1000, 2)
                logger.exception(
                    "http.unhandled_exception %s %s duration_ms=%.2f",
                    method,
                    path,
                    duration_ms,
                    extra={"route": route, "status_code": 500,
                           "duration_ms": duration_ms},
                )
                raise
            finally:
                elapsed = max(time.monotonic() - started, 0.0)
                HTTP_REQUESTS_IN_PROGRESS.labels(
                    method=method, route=in_progress_route).dec()
                final_route = _route_label(request)
                HTTP_REQUESTS_TOTAL.labels(
                    method=method, route=final_route, status_code=str(
                        status_code)
                ).inc()
                HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=final_route).observe(
                    elapsed
                )
                duration_ms = round(elapsed * 1000, 2)
                slow_ms = float(os.getenv("WUWA_SLOW_REQUEST_MS", "1000"))
                log_all_http = os.getenv("WUWA_LOG_HTTP_REQUESTS", "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                }
                is_slow = path not in ("/health", "/metrics") and duration_ms >= slow_ms
                if status_code >= 400 or is_slow or (log_all_http and path not in ("/health", "/metrics")):
                    log_fn = (
                        logger.error
                        if status_code >= 500
                        else logger.warning
                        if status_code >= 400 or is_slow
                        else logger.info
                    )
                    log_fn(
                        "http.request %s %s status=%d duration_ms=%.2f",
                        method,
                        path,
                        status_code,
                        duration_ms,
                        extra={
                            "route": final_route,
                            "status_code": status_code,
                            "duration_ms": duration_ms,
                            **_endpoint_source(request),
                        },
                    )
