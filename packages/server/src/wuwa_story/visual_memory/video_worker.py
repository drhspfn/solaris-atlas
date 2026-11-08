"""Single CPU consumer of durable visual-memory scans; safe to restart between frames."""

import asyncio
import logging
import signal
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models import ProcessingRun, Processor
from wuwa_story.db.session import engine
from wuwa_story.visual_memory.video import PROCESSOR, VideoReviewService

logger = logging.getLogger(__name__)


async def run_once(db_engine=engine, *, storage=None, inference=None):
    async with db_engine.connect() as connection:
        async with AsyncSession(connection, expire_on_commit=False) as session:
            ids = (
                await session.scalars(
                    select(ProcessingRun.id)
                    .join(Processor)
                    .where(
                        Processor.key == PROCESSOR,
                        ProcessingRun.status.in_(("pending", "running")),
                    )
                    .order_by(ProcessingRun.id)
                    .limit(10)
                )
            ).all()
        await connection.rollback()
        for scan_id in ids:
            locked = await connection.scalar(
                text("SELECT pg_try_advisory_lock(813, :id)"), {"id": scan_id}
            )
            await connection.commit()
            if not locked:
                continue
            try:
                async with AsyncSession(
                    connection, expire_on_commit=False, autoflush=False
                ) as session:
                    service = VideoReviewService(session, storage, inference)
                    try:
                        logger.info("visual_video.started scan=%s", scan_id)
                        await service.process(scan_id)
                        logger.info("visual_video.completed scan=%s", scan_id)
                    except Exception as exc:
                        await session.rollback()
                        run = await service._run(scan_id)
                        run.status, run.finished_at = "failed", datetime.now(UTC)
                        # Stable operator diagnostic, without subprocess output or SQL payloads.
                        run.error = (
                            f"{type(exc).__name__}: {str(exc)[:500]}"
                            if isinstance(exc, ValueError)
                            else f"{type(exc).__name__}: scan failed; inspect worker logs"
                        )
                        await session.commit()
                        logger.exception("visual_video.failed scan=%s", scan_id)
                return True
            finally:
                await connection.rollback()
                await connection.execute(
                    text("SELECT pg_advisory_unlock(813, :id)"), {"id": scan_id}
                )
                await connection.commit()
    return False


async def watch():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    # SIGTERM stops claiming jobs. If the process is killed while sampling,
    # PostgreSQL releases the session lease and a replacement resumes checkpoints.
    while not stop.is_set():
        try:
            worked = await run_once()
        except Exception:
            worked = False
            logger.exception("visual_video.poll_failed")
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
    await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(watch())
