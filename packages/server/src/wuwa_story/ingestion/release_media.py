"""Durable media fan-out for an imported snapshot; visual analysis is separate."""

import hashlib
import json

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONPATH, insert

from wuwa_story.db.models.core import Cutscene, QuestAction, QuestState, VoiceReference
from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.db.models.ops import GameRelease, ProcessingRun, Processor
from wuwa_story.ingestion.media_jobs import publish_media_job
from wuwa_story.ingestion.media_workspaces import retain_workspace, workspace_expired

QUEUE = "wuwa.release-media.v1"
EVENT_QUEUE = "wuwa.event-media.v1"
PROCESSOR = "release_media"


def batches(values, size=100):
    return [values[start:start + size] for start in range(0, len(values), size)]


async def enqueue_release_media(
    session, release_id: int, *, event_artwork_only: bool = False, notices_only: bool = False,
    illustrations_only: bool = False,
    story_structure_only: bool = False,
    story_media_only: bool = False,
    repair_story: bool = False,
) -> dict:
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Imported snapshot not found")
    await session.execute(text("SELECT pg_advisory_xact_lock(701, :id)"), {"id": release_id})
    # Preserve callers' pending output resets before refreshing matched runs.
    await session.flush()
    await session.execute(insert(Processor).values(key=PROCESSOR, version="1")
                          .on_conflict_do_nothing(index_elements=[Processor.key]))
    processor = await session.scalar(select(Processor.id).where(Processor.key == PROCESSOR))
    if event_artwork_only or notices_only or story_structure_only:
        entities, cutscenes, voices = [], [], []
    else:
        present = select(NodeRevision.node_id).where(
            NodeRevision.release_id == release_id)
        illustrated = select(QuestState.node_id).join(QuestAction,
                                                      QuestAction.quest_state_node_id == QuestState.node_id).where(
            func.jsonb_path_exists(QuestAction.params, cast('$.**.BackgroundConfig', JSONPATH))
            | func.jsonb_path_exists(QuestAction.params, cast('$.**.SequenceDataAsset', JSONPATH))
            | (QuestAction.action_name == 'PlaySequenceData'))
        catalog = ((Node.canonical_key.startswith("character:"))
                   | (Node.canonical_key.startswith("item:")) | (Node.canonical_key.startswith("skill:")))
        entities = list(await session.scalars(select(Node.canonical_key).where(
            Node.id.in_(present), Node.id.in_(illustrated) if illustrations_only or story_media_only else
            catalog | Node.id.in_(illustrated)).order_by(Node.id)))
        cutscenes, voices = [], []
        if not illustrations_only:
            cutscenes = list(await session.scalars(select(Cutscene.cg_name).where(
                Cutscene.node_id.in_(present), Cutscene.cg_name.is_not(None)).order_by(Cutscene.node_id)))
            if not story_media_only:
                voices = list(await session.scalars(select(VoiceReference.file_name).where(
                    VoiceReference.node_id.in_(present), VoiceReference.file_name.is_not(None))
                    .distinct().order_by(VoiceReference.file_name)))
    characters = [key for key in entities if key.startswith("character:")]
    if illustrations_only:
        cutscenes, voices, characters = [], [], []
    if story_media_only:
        voices, characters = [], []
    specs = [("prepare", [])]
    if notices_only:
        specs.append(("event_notices", ["en"]))
    if event_artwork_only:
        # Discover the complete catalog in the client, including events that
        # have never appeared in a community timetable. Images fan out afterwards.
        specs.append(("event_catalog", ["client-catalog:v2"]))
    if not (event_artwork_only or notices_only) and cutscenes:
        specs.append(("cutscene_assets", sorted(set(cutscenes))))
    if not (event_artwork_only or notices_only) and (voices or characters or story_media_only and cutscenes):
        specs.append(("voice_packages", []))
    if not (event_artwork_only or notices_only):
        if not illustrations_only and not story_media_only:
            specs.append(("tree_order", []))
        specs.extend(("images", batch) for batch in batches(entities))
        specs.extend(("cutscene", [name]) for name in sorted(set(cutscenes)))
        specs.extend(("voices", batch) for batch in batches(voices))
        specs.extend(("character_voices", batch)
                     for batch in batches(characters, size=1))
    pending, runs = [], []
    parent_id = None
    dependencies = {}
    refresh_files = False
    resume_story_media = False
    for kind, targets in specs:
        dependency = dependencies.get("event_catalog" if kind == "event_images" else "cutscene_assets" if kind ==
                                      "cutscene" else "voice_packages" if kind in {"voices", "character_voices"} else "prepare")
        request = {"release_id": release_id, "game_version": release.game_version,
                   "kind": kind, "targets": targets, "parent_id": dependency}
        if kind == 'tree_order':
            request['index_schema'] = 3
            if repair_story:
                request['repair_story'] = 1
        if kind == 'images' and any(key.startswith('flow_state:') for key in targets):
            request['illustration_schema'] = 5
        digest = hashlib.sha256(json.dumps(
            request, sort_keys=True).encode()).digest()
        run = await session.scalar(select(ProcessingRun).where(
            ProcessingRun.processor_id == processor, ProcessingRun.input_hash == digest)
            .order_by(ProcessingRun.id.desc()).limit(1)
            .execution_options(populate_existing=True))
        if run is None:
            run = ProcessingRun(processor_id=processor, input_hash=digest)
            session.add(run)
            await session.flush()
        dependencies[kind] = run.id
        if kind == "prepare":
            parent_id = run.id
            await retain_workspace(session, parent_id)
            refresh_files = run.status == "completed" and workspace_expired(
                run)
        # A root can already be renewed while its completed package coordinators
        # still carry legacy results. Renew those without resetting good leaves.
        if run.status == "completed" and (refresh_files or (
            kind in {"cutscene_assets", "voice_packages"} and workspace_expired(run)
        )):
            run.status = "pending"
            run.raw_output = None
        queue = EVENT_QUEUE if event_artwork_only or notices_only else QUEUE
        if kind == "images":
            queue = "wuwa.release-images.v1"
        if (event_artwork_only and kind == "event_catalog" or notices_only and kind == "event_notices") and run.status == "completed":
            # An explicit reimport must revisit failed/partial image children,
            # while successful extraction work remains reusable.
            run.status = "pending"
        already_queued = run.status == "queued" and (
            run.metadata_json or {}).get("queue", QUEUE) == queue
        if run.status not in {"running", "completed", "waiting_dependency"} and not already_queued:
            payload = {"schema_version": 1, "job_type": "media.release", "run_id": run.id,
                       **request}
            if refresh_files:
                payload["recheck_files"] = True
            if run.status in {"partial", "failed", "blocked"}:
                run.raw_output = None
            run.status, run.error, run.finished_at = "queued", None, None
            run.metadata_json = {**(run.metadata_json or {}), "payload": payload, "request": request,
                                 "queue": queue}
            pending.append((run, payload, queue))
        runs.append(run)
        if kind == 'tree_order' and repair_story and run.status == 'completed':
            resume_story_media = True
    await session.commit()
    failed = 0
    for run, payload, queue in pending:
        try:
            await publish_media_job(payload, f"release-media:{run.id}", queue)
        except Exception:
            run.status = "enqueue_failed"
            run.error = "Broker did not confirm publication; repeat the media import to retry"
            await session.commit()
            failed += 1
    report = {"release_id": release_id, "parent_id": parent_id, "tasks": len(runs),
              "queued": len(pending) - failed, "enqueue_failed": failed}
    if resume_story_media:
        report['story_media'] = await enqueue_release_media(session, release_id, story_media_only=True)
    return report


async def enqueue_event_images(session, payload: dict, paths: list[str], *, kind="event_images") -> dict:
    """Bound image tasks and pin their dependency to this exact client catalog."""
    parent = await session.get(ProcessingRun, payload["run_id"])
    if parent is None:
        raise ValueError("Event catalog task not found")
    workspace_id = (parent.raw_output or {}).get(
        "workspace_id") if getattr(parent, "raw_output", None) else None
    if workspace_id is not None:
        await retain_workspace(session, workspace_id)
    pending = []
    runs = []
    if kind not in {"event_images", "event_resources", "event_notices"}:
        raise ValueError("Unsupported event asset task")
    for targets in batches(sorted(set(paths)), size=1 if kind == "event_resources" else 20):
        request = {"release_id": payload["release_id"], "game_version": payload["game_version"],
                   "kind": kind, "targets": targets, "parent_id": parent.id,
                   "catalog_revision": 2}
        digest = hashlib.sha256(json.dumps(
            request, sort_keys=True).encode()).digest()
        run = await session.scalar(select(ProcessingRun).where(
            ProcessingRun.processor_id == parent.processor_id, ProcessingRun.input_hash == digest))
        if run is None:
            run = ProcessingRun(
                processor_id=parent.processor_id, input_hash=digest)
            session.add(run)
            await session.flush()
        if (kind == "event_notices" or payload.get("recheck_files")) and run.status == "completed":
            # Notices are live content; an explicit catalog refresh revisits the feed.
            run.status = "pending"
        if run.status not in {"running", "completed", "queued", "waiting_dependency"}:
            job = {"schema_version": 1, "job_type": "media.release",
                   "run_id": run.id, **request}
            run.status, run.error, run.finished_at, run.raw_output = "queued", None, None, None
            run.metadata_json = {"payload": job,
                                 "request": request, "queue": EVENT_QUEUE}
            pending.append((run, job))
        runs.append(run)
    await session.commit()
    failed = 0
    for run, job in pending:
        try:
            await publish_media_job(job, f"release-media:{run.id}", EVENT_QUEUE)
        except Exception:
            run.status, run.error = "enqueue_failed", "Event image publication failed; retry catalog import"
            await session.commit()
            failed += 1
    if failed:
        raise RuntimeError(
            f"Could not enqueue {failed} event image tasks; retry catalog import")
    return {"tasks": len(runs), "queued": len(pending)}
