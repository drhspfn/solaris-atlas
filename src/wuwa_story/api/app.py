from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from wuwa_story.api.errors import install_error_handlers
from wuwa_story.api.routes import health, nodes, releases, search, story
from wuwa_story.config.logging import configure_logging
from wuwa_story.config.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
app.include_router(health.router)
app.include_router(releases.router)
app.include_router(nodes.router)
app.include_router(search.router)
app.include_router(story.router)
