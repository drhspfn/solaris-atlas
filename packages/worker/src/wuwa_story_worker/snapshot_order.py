"""Serialize global database projections while snapshots compile in parallel."""

from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wuwa_story.db.models.ops import ProcessingRun, Processor

from wuwa_story_worker.broker import JobDeferred, job_lock


def earlier_snapshot(requests, version, run_id):
    current = tuple(map(int, version.split(".")))
    for candidate_id, metadata in requests:
        candidate = (metadata or {}).get("request", {}).get("version")
        if candidate_id == run_id or not isinstance(candidate, str):
            continue
        try:
            parsed = tuple(map(int, candidate.split(".")))
        except ValueError:
            continue
        if parsed < current:
            return candidate
    return None


@asynccontextmanager
async def ordered_import(engine, version, run_id):
    # Entity and search tables are shared projections. Holding a session lock on
    # a separate connection keeps batch commits from releasing the import lease.
    async with engine.connect() as connection:
        async with job_lock(connection, 527631, 1):
            async with AsyncSession(bind=connection) as session:
                rows = (await session.execute(
                    select(ProcessingRun.id, ProcessingRun.metadata_json)
                    .join(Processor, Processor.id == ProcessingRun.processor_id)
                    .where(Processor.key == "snapshot_import",
                           ProcessingRun.status.in_(["queued", "running", "waiting_dependency"]))
                )).all()
                if previous := earlier_snapshot(rows, version, run_id):
                    raise JobDeferred(f"Waiting for earlier snapshot {previous}")
                await session.commit()
            yield
