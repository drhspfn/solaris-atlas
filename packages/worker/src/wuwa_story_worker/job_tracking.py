"""Track admin processing runs across worker jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wuwa_story.config.settings import get_settings


async def update_admin_run(
    run_id: int,
    processor_key: str,
    status: str,
    *,
    error: str | None = None,
    raw_output: dict[str, Any] | None = None,
) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            from wuwa_story.db.models.ops import ProcessingRun, Processor

            run = await session.scalar(
                select(ProcessingRun)
                .join(Processor, Processor.id == ProcessingRun.processor_id)
                .where(ProcessingRun.id == run_id, Processor.key == processor_key)
            )
            if run is None:
                return
            run.status = status
            run.error = error
            if raw_output is not None:
                run.raw_output = raw_output
            if status in {"completed", "failed"}:
                run.finished_at = datetime.now(UTC)
            await session.commit()
    finally:
        await engine.dispose()
