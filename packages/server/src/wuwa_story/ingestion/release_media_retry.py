"""Fresh, bounded retry graphs; existing release runs remain immutable audit history."""

import hashlib
import json
import logging

from sqlalchemy import select, text

from wuwa_story.db.models.ops import GameRelease, ProcessingRun, Processor
from wuwa_story.ingestion.media_jobs import publish_media_job
from wuwa_story.ingestion.media_workspaces import retain_workspace, workspace_expired
from wuwa_story.ingestion.release_media import EVENT_QUEUE, PROCESSOR, QUEUE

logger = logging.getLogger(__name__)
DEFAULT_STATUSES = {"cutscene": {"failed", "partial", "blocked"},
                    "images": {"failed", "blocked"}, "event_catalog": {"failed", "blocked"}}
ACTIVE = {"pending", "queued", "running", "waiting_dependency"}


def _request(run):
    metadata = run.metadata_json or {}
    return metadata.get("request") or metadata.get("payload") or {}


async def _new_run(session, processor, release, kind, targets, parent_id, retry_of, root_id=None):
    request = {"release_id": release.id, "game_version": release.game_version,
               "kind": kind, "targets": targets, "parent_id": parent_id}
    # A prepare has no parent: source attempt IDs distinguish it from a full
    # release import and from later attempts after a retry itself fails.
    identity = {"request": request, "retry_of": retry_of} if kind == "prepare" else request
    run = ProcessingRun(processor_id=processor, status="pending",
                        input_hash=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).digest())
    session.add(run)
    await session.flush()
    queue = "wuwa.release-images.v1" if kind == "images" else EVENT_QUEUE if kind == "event_catalog" else QUEUE
    run.metadata_json = {"request": request, "queue": queue, "retry_of": retry_of,
                         "retry_root_id": root_id or run.id,
                         "payload": {"schema_version": 1, "job_type": "media.release", "run_id": run.id,
                                     **request}}
    return run


async def _publish_pending(session, release_id, runs):
    """Recover a crash between graph COMMIT and publication without duplicating ACKed jobs.

    Reacquire the release lock after the graph is durable. Concurrent requests
    serialize here and recheck row state; only pending rows are published. Worker
    claims use row locks too, so no running/completed state is overwritten.
    """
    if not runs:
        return 0, 0
    await session.execute(text("SELECT pg_advisory_xact_lock(701, :id)"), {"id": release_id})
    for root_id in sorted({run.metadata_json["retry_root_id"] for run in runs}):
        await retain_workspace(session, root_id)
    pending = list(await session.scalars(select(ProcessingRun).where(
        ProcessingRun.id.in_([run.id for run in runs]), ProcessingRun.status == "pending",
    ).order_by(ProcessingRun.id).with_for_update().execution_options(populate_existing=True)))
    published = failed = 0
    for run in pending:
        try:
            await publish_media_job(run.metadata_json["payload"], f"release-media:{run.id}", run.metadata_json["queue"])
        except Exception:
            run.status, run.error = "enqueue_failed", "Broker did not confirm publication; repeat targeted retry"
            failed += 1
            logger.exception("release_media.retry_enqueue_failed release=%s run=%s", release_id, run.id)
        else:
            run.status, run.error = "queued", None
            published += 1
    await session.commit()
    return published, failed


async def enqueue_failed_release_media(session, release_id: int, *, kinds: set[str] | None = None,
                                       statuses: set[str] | None = None) -> dict:
    """Retry latest failed logical tasks without resetting successful release work.

    Defaults: failed/partial cutscenes and failed images/event catalogs, including
    leaves blocked by a failed prerequisite. Explicit
    statuses may select failed/partial for any requested supported kind. Active
    retry graphs are reused; unconfirmed publication is retried in the same graph
    while its workspace is valid. A failed retry leaf becomes a new source attempt.

    Result parent_ids are prepare/workspace IDs; run_ids include prerequisites.
    retry_of lists newly selected source IDs, reused counts existing graph jobs,
    and queued/enqueue_failed count this call's broker publication outcomes.

    Newly allocated runs are pending until confirmed publication, then queued;
    publication failures are enqueue_failed. Processing uses the existing worker
    statuses and request format. No live jobs are cancelled or reset here.
    """
    kinds = set(DEFAULT_STATUSES) if kinds is None else set(kinds)
    if not kinds or not kinds <= DEFAULT_STATUSES.keys():
        raise ValueError("Retry kinds must be cutscene, images or event_catalog")
    if statuses is not None and (not statuses or not set(statuses) <= {"failed", "partial"}):
        raise ValueError("Retry statuses must be failed or partial")
    if type(release_id) is not int or release_id < 1:
        raise ValueError("release_id must be a positive ID")
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Imported snapshot not found")
    await session.execute(text("SELECT pg_advisory_xact_lock(701, :id)"), {"id": release_id})
    processor = await session.scalar(select(Processor.id).where(Processor.key == PROCESSOR))
    rows = [] if processor is None else list(await session.scalars(select(ProcessingRun).where(
        ProcessingRun.processor_id == processor,
        ProcessingRun.metadata_json["request"]["release_id"].astext == str(release_id),
    ).order_by(ProcessingRun.id)))
    graphs, latest = {}, {}
    for run in rows:
        root_id = (run.metadata_json or {}).get("retry_root_id")
        if root_id:
            graphs.setdefault(root_id, []).append(run)
        request = _request(run)
        if request.get("kind") in kinds:
            key = (request["kind"], json.dumps(sorted(request.get("targets") or [])))
            latest[key] = run
    sources, reused_roots = [], set()
    for run in latest.values():
        if run.status == "completed":
            continue
        kind = _request(run)["kind"]
        eligible = run.status in (set(statuses) if statuses is not None else DEFAULT_STATUSES[kind])
        root_id = (run.metadata_json or {}).get("retry_root_id")
        graph = graphs.get(root_id, [])
        recoverable = any(item.status == "enqueue_failed" for item in graph)
        root = next((item for item in graph if item.id == root_id), None)
        active = any(item.status in ACTIVE for item in graph)
        if graph and (eligible or run.status in ACTIVE or recoverable):
            if active or recoverable and root is not None and not workspace_expired(root):
                reused_roots.add(root_id)
                continue
            if recoverable and run.status != "completed":
                # A failed publication may have blocked its children before GC
                # removed the old scratch. Rebuild only those unfinished leaves.
                sources.append(run)
                continue
        if eligible:
            sources.append(run)
    runs = []
    for root_id in sorted(reused_roots):
        await retain_workspace(session, root_id)
        recovering_publication = any(run.status == "enqueue_failed" for run in graphs[root_id])
        graph = list(await session.scalars(select(ProcessingRun).where(
            ProcessingRun.id.in_([run.id for run in graphs[root_id]]),
        ).order_by(ProcessingRun.id).with_for_update().execution_options(populate_existing=True)))
        root = next(run for run in graph if run.id == root_id)
        if not any(run.status in ACTIVE for run in graph) and workspace_expired(root):
            # Recheck after the shared lease: GC could win between the first
            # filesystem check and lock acquisition. Do not resume stale parents.
            reused_roots.remove(root_id)
            latest_ids = {run.id for run in latest.values()}
            sources.extend(run for run in graph if run.id in latest_ids and run.status != "completed")
            continue
        # Only this fresh retry graph is resumed, never original release runs.
        # pending survives a publisher crash; enqueue_failed means no broker ACK.
        recovering_publication |= any(run.status == "enqueue_failed" for run in graph)
        for run in graph:
            if run.status == "enqueue_failed" or recovering_publication and run.status == "blocked":
                run.status, run.error, run.finished_at = "pending", None, None
        runs.extend(graph)
    sources.sort(key=lambda run: run.id)
    if sources:
        root = await _new_run(session, processor, release, "prepare", [], None, [run.id for run in sources])
        await retain_workspace(session, root.id)
        runs.append(root)
        cutscenes = [run for run in sources if _request(run)["kind"] == "cutscene"]
        assets = None
        if cutscenes:
            source_ids = [run.id for run in cutscenes]
            targets = sorted({target for run in cutscenes for target in _request(run)["targets"]})
            assets = await _new_run(session, processor, release, "cutscene_assets", targets, root.id, source_ids, root.id)
            voices = await _new_run(session, processor, release, "voice_packages", [], root.id, source_ids, root.id)
            runs.extend([assets, voices])
        for source in sources:
            request = _request(source)
            runs.append(await _new_run(session, processor, release, request["kind"], list(request["targets"]),
                                       assets.id if request["kind"] == "cutscene" else root.id, source.id, root.id))
    # GC and broker consumers see every prerequisite and descendant atomically.
    await session.commit()
    queued, failed = await _publish_pending(session, release_id, runs)
    return {"release_id": release_id, "parent_ids": sorted({run.metadata_json["retry_root_id"] for run in runs}),
            "run_ids": [run.id for run in runs], "retry_of": [run.id for run in sources],
            "tasks": len(runs), "reused": sum(len(graphs[root_id]) for root_id in reused_roots),
            "queued": queued, "enqueue_failed": failed}
