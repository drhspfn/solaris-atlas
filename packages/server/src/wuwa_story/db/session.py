import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from wuwa_story.config.settings import get_settings

_pool_options = {}
if any(key in os.environ for key in ("WUWA_WORKER_DB_POOL_SIZE", "WUWA_WORKER_DB_MAX_OVERFLOW")):
    _pool_size = int(os.getenv("WUWA_WORKER_DB_POOL_SIZE", "4"))
    _max_overflow = int(os.getenv("WUWA_WORKER_DB_MAX_OVERFLOW", "4"))
    if _pool_size < 1 or _max_overflow < 0:
        raise ValueError("Worker DB pool size must be positive and overflow nonnegative")
    _pool_options = {"pool_size": _pool_size, "max_overflow": _max_overflow}

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, **_pool_options)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
