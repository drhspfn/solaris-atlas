"""Queue pinned snapshot imports from the console or the production watcher."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.ops import GameRelease, ImportRun, ProcessingRun, Processor
from wuwa_story.ingestion.media_jobs import publish_media_job

DEFAULT_REPOSITORY = "https://github.com/Arikatsu/WutheringWaves_Data.git"


class SnapshotQueueUnavailable(RuntimeError):
    pass


async def enqueue_pinned_snapshot(session, snapshot, *, automatic=False, publish=publish_media_job):
    identity = hashlib.sha256(
        f"{DEFAULT_REPOSITORY}:{snapshot.branch}:{snapshot.commit}".encode()
    ).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": int.from_bytes(identity[:7], "big")},
    )
    await session.execute(
        insert(Processor)
        .values(
            key="snapshot_import",
            version="1",
            description="Build and import a pinned upstream snapshot",
        )
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor_id = await session.scalar(
        select(Processor.id).where(Processor.key == "snapshot_import")
    )
    assert processor_id is not None
    run = await session.scalar(
        select(ProcessingRun)
        .where(ProcessingRun.processor_id == processor_id, ProcessingRun.input_hash == identity)
        .order_by(ProcessingRun.id.desc())
        .limit(1)
    )
    if run is None:
        if automatic:
            imported = await session.scalar(
                select(ImportRun.id).join(GameRelease)
                .where(GameRelease.upstream_commit == snapshot.commit, ImportRun.status == "succeeded")
                .limit(1)
            )
            if imported is not None:
                return {"id": None, "status": "imported", "version": snapshot.branch, "commit": snapshot.commit}
        run = ProcessingRun(processor_id=processor_id, input_hash=identity)
        session.add(run)
        await session.flush()
    elif run.status in {"queued", "running", "completed"} or (
        automatic and run.status not in {None, "pending", "enqueue_failed"}
    ):
        return {
            "id": run.id,
            "status": run.status,
            "version": snapshot.branch,
            "commit": snapshot.commit,
        }
    run.status, run.error, run.finished_at = "queued", None, None
    run.metadata_json = {
        "request": {
            "version": snapshot.branch,
            "commit": snapshot.commit,
            "repository": DEFAULT_REPOSITORY,
        }
    }
    payload = {
        "schema_version": 1,
        "job_type": "snapshot.build_import",
        "version": snapshot.branch,
        "repository_url": DEFAULT_REPOSITORY,
        "branch": snapshot.branch,
        "commit": snapshot.commit,
        "run_id": run.id,
    }
    await session.commit()
    try:
        await publish(payload, f"snapshot-import:{run.id}", "wuwa.snapshot-build.v1")
    except Exception as error:
        run.status = "enqueue_failed"
        run.error = "Queue confirmation failed; retry this snapshot import"
        await session.commit()
        raise SnapshotQueueUnavailable from error
    return {
        "id": run.id,
        "status": run.status,
        "version": snapshot.branch,
        "commit": snapshot.commit,
        "queued_at": datetime.now(UTC),
    }
