"""Poll upstream release branches and enqueue immutable snapshot jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from wuwa_story.ingestion.github_snapshots import (
    discover_remote_snapshots,
    load_checkpoints,
    save_checkpoint,
)

from wuwa_story_worker.broker import publish_job

logger = logging.getLogger(__name__)
DEFAULT_REPOSITORY = "https://github.com/Arikatsu/WutheringWaves_Data.git"


async def enqueue_snapshot(version: str, repository: str, commit: str | None = None) -> None:
    if commit is None:
        snapshots = discover_remote_snapshots(repository, version, version)
        snapshot = snapshots[0]
        commit = snapshot.commit
    else:
        snapshot = None
    await publish_job(
        "snapshot_build",
        {
            "schema_version": 1,
            "job_type": "snapshot.build_import",
            "version": version,
            "repository_url": repository,
            "branch": snapshot.branch if snapshot else version,
            "commit": commit,
        },
        message_id=f"snapshot:{version}:{commit}",
    )
    logger.info("Enqueued WuWa %s at %s", version, commit)


async def poll_upstream(
    *, repository: str, first: str, last: str | None, checkpoint_path: Path
) -> int:
    snapshots = discover_remote_snapshots(repository, first, last)
    checkpoints = load_checkpoints(checkpoint_path)
    queued = 0
    for snapshot in snapshots:
        if checkpoints.get(snapshot.branch) == snapshot.commit:
            continue
        await publish_job(
            "snapshot_build",
            {
                "schema_version": 1,
                "job_type": "snapshot.build_import",
                "version": snapshot.branch,
                "repository_url": repository,
                "branch": snapshot.branch,
                "commit": snapshot.commit,
            },
            message_id=f"snapshot:{snapshot.branch}:{snapshot.commit}",
        )
        save_checkpoint(checkpoint_path, snapshot.branch, snapshot.commit)
        checkpoints[snapshot.branch] = snapshot.commit
        queued += 1
        logger.info("Queued upstream WuWa %s at %s", snapshot.branch, snapshot.commit)
    return queued


async def watch_upstream(
    *, repository: str, first: str, last: str | None, interval: int, workspace: Path
) -> None:
    if interval < 10:
        raise ValueError("Polling interval must be at least 10 seconds")
    checkpoint_path = workspace / "published-checkpoints.json"
    workspace.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            count = await poll_upstream(
                repository=repository,
                first=first,
                last=last,
                checkpoint_path=checkpoint_path,
            )
            logger.info("Upstream scan queued %d snapshot(s)", count)
        except Exception:
            logger.exception("Upstream scan failed; retrying on the next poll")
        await asyncio.sleep(interval)


async def run_watch(args) -> None:
    workspace = Path(args.workspace).resolve()
    if args.once:
        workspace.mkdir(parents=True, exist_ok=True)
        count = await poll_upstream(
            repository=args.repo_url,
            first=args.from_version,
            last=args.to_version,
            checkpoint_path=workspace / "published-checkpoints.json",
        )
        logger.info("Upstream scan queued %d snapshot(s)", count)
        return
    await watch_upstream(
        repository=args.repo_url,
        first=args.from_version,
        last=args.to_version,
        interval=args.interval_seconds,
        workspace=workspace,
    )
