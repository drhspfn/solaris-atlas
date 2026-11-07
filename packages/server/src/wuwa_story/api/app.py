import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.cors import CORSMiddleware

from wuwa_story.api.admission import RequestAdmission
from wuwa_story.api.cache import PublicResponseCache
from wuwa_story.api.errors import install_error_handlers
from wuwa_story.api.routes import (
    admin_data,
    character_models,
    cutscenes,
    game_events,
    graph_paths,
    health,
    maps,
    mcp,
    media_jobs,
    nodes,
    notices,
    read_projections,
    releases,
    search,
    seo,
    story,
    story_agent,
)
from wuwa_story.auth.routes import router as auth_router
from wuwa_story.auth.services import AuthError
from wuwa_story.cache import cache_lifespan
from wuwa_story.config.logging import configure_logging
from wuwa_story.config.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("wuwa_story.auth")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from wuwa_story.api.routes.mcp import streamable_http

    async with cache_lifespan(settings) as cache:
        _app.state.cache = cache
        _app.state.agent_query_redis = cache.redis
        try:
            async with streamable_http.run():
                yield
        finally:
            _app.state.cache = None
            _app.state.agent_query_redis = None


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    servers=[{"url": settings.api_url.rstrip("/")}],
    swagger_ui_parameters={"withCredentials": True},
)
# Cached hits bypass admission; origin work leaves capacity for health/revision reads.
app.add_middleware(
    RequestAdmission, limit=settings.api_max_concurrent_requests)
app.add_middleware(PublicResponseCache, settings=settings)
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
            request.scope.get("query_string", b"").decode(
                "ascii", errors="ignore"),
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
app.include_router(game_events.router)
app.include_router(notices.router)
app.include_router(admin_data.router)
app.include_router(read_projections.router)
app.include_router(maps.router)
app.include_router(media_jobs.router)
app.include_router(character_models.router)
app.include_router(cutscenes.router)
app.include_router(nodes.router)
app.include_router(graph_paths.router)
app.include_router(search.router)
app.include_router(seo.router)
app.include_router(story.router)
app.include_router(story_agent.router)
app.include_router(story_agent.admin)
app.include_router(auth_router)

app.include_router(mcp.router)
app.mount("/mcp", mcp.streamable_http.handle_request)


from wuwa_story.admin.composition import install_admin  # noqa: E402
from wuwa_story.db.session import engine  # noqa: E402

console = install_admin(app, engine)
