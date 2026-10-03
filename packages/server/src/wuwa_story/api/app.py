import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from starlette.middleware.cors import CORSMiddleware

from wuwa_story.api.cache import PublicResponseCache
from wuwa_story.api.errors import install_error_handlers
from wuwa_story.api.routes import (
    graph_paths,
    health,
    maps,
    media_jobs,
    nodes,
    releases,
    search,
    story,
)
from wuwa_story.auth.routes import router as auth_router
from wuwa_story.auth.services import AuthError
from wuwa_story.config.logging import configure_logging
from wuwa_story.config.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("wuwa_story.auth")
response_cache = Redis.from_url(
    settings.redis_url.get_secret_value(),
    socket_connect_timeout=settings.api_cache_timeout_seconds,
    socket_timeout=settings.api_cache_timeout_seconds,
    max_connections=50,
    retry=Retry(NoBackoff(), 0),
) if settings.redis_url else None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        if response_cache is not None:
            await response_cache.aclose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(PublicResponseCache, settings=settings, redis=response_cache)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
install_error_handlers(app)


@app.middleware("http")
async def keep_oauth_callback_credentials_out_of_access_logs(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.url.path == "/auth/google/callback":
        request.state.oauth_query = parse_qs(
            request.scope.get("query_string", b"").decode("ascii", errors="ignore"),
            keep_blank_values=True,
        )
        # Keep Uvicorn access logs from recording Google's one-time code and OAuth state.
        request.scope["query_string"] = b""
    return await call_next(request)


@app.exception_handler(AuthError)
async def auth_error_handler(_request: Request, exc: AuthError) -> JSONResponse:
    logger.info("request.failed code=%s status=%s", exc.code, exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


app.include_router(health.router)
app.include_router(releases.router)
app.include_router(maps.router)
app.include_router(media_jobs.router)
app.include_router(nodes.router)
app.include_router(graph_paths.router)
app.include_router(search.router)
app.include_router(story.router)
app.include_router(auth_router)
