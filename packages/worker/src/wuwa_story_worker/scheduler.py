"""Poll upstream release branches and enqueue immutable snapshot jobs."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.client_assets import inspect_installed_clients
from wuwa_story.ingestion.github_snapshots import (
    discover_remote_snapshots,
    load_checkpoints,
    save_checkpoint,
)
from wuwa_story.ingestion.snapshot_queue import DEFAULT_REPOSITORY, enqueue_pinned_snapshot

from wuwa_story_worker.broker import publish_job
from wuwa_story_worker.tooling import asset_workspace

logger = logging.getLogger(__name__)


async def poll_latest_snapshot() -> None:
    clients = await asyncio.to_thread(inspect_installed_clients, asset_workspace())
    current = next((client["version"] for client in clients if client["state"] == "downloaded"), None)
    if current is None:
        logger.info("snapshot.poll status=waiting_for_client")
        return
    # The latest verified download gates publication of a new major/minor patch.
    branch = ".".join(current.split(".")[:2])
    snapshot = (await asyncio.to_thread(
        discover_remote_snapshots, DEFAULT_REPOSITORY, branch, branch,
    ))[0]
    async with SessionFactory() as session:
        result = await enqueue_pinned_snapshot(session, snapshot, automatic=True)
    logger.info("snapshot.poll branch=%s commit=%s run=%s status=%s",
                snapshot.branch, snapshot.commit, result["id"], result["status"])


async def watch_latest_snapshot() -> None:
    interval = int(os.getenv("WUWA_SNAPSHOT_UPDATE_INTERVAL_SECONDS", "0"))
    if interval < 60:
        raise ValueError("Snapshot poll interval must be at least 60 seconds")
    while True:
        try:
            await poll_latest_snapshot()
        except Exception:
            logger.exception("Automatic snapshot check failed; retrying next interval")
        await asyncio.sleep(interval)


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
