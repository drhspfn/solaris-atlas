"""Durable media fan-out for an imported snapshot; visual analysis is separate."""

import hashlib
import json

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.db.models.core import Cutscene, VoiceReference
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.db.models.ops import GameRelease, ProcessingRun, Processor
from wuwa_story.ingestion.media_jobs import publish_media_job

QUEUE = "wuwa.release-media.v1"
EVENT_QUEUE = "wuwa.event-media.v1"
PROCESSOR = "release_media"


def batches(values, size=100):
    return [values[start:start + size] for start in range(0, len(values), size)]


async def enqueue_release_media(
    session, release_id: int, *, event_artwork_only: bool = False
) -> dict:
    release = await session.get(GameRelease, release_id)
    if release is None:
        raise ValueError("Imported snapshot not found")
    await session.execute(text("SELECT pg_advisory_xact_lock(701, :id)"), {"id": release_id})
    await session.execute(insert(Processor).values(key=PROCESSOR, version="1")
                          .on_conflict_do_nothing(index_elements=[Processor.key]))
    processor = await session.scalar(select(Processor.id).where(Processor.key == PROCESSOR))
    if event_artwork_only:
        entities, cutscenes, voices = [], [], []
    else:
        present = select(NodeRevision.node_id).where(NodeRevision.release_id == release_id)
        entities = list(await session.scalars(select(Node.canonical_key).where(
            Node.id.in_(present), (Node.canonical_key.startswith("character:"))
            | (Node.canonical_key.startswith("item:"))
            | (Node.canonical_key.startswith("skill:"))).order_by(Node.id)))
        cutscenes = list(await session.scalars(select(Cutscene.cg_name).where(
            Cutscene.node_id.in_(present), Cutscene.cg_name.is_not(None)).order_by(Cutscene.node_id)))
        voices = list(await session.scalars(select(VoiceReference.file_name).where(
            VoiceReference.node_id.in_(present), VoiceReference.file_name.is_not(None))
            .distinct().order_by(VoiceReference.file_name)))
    characters = [key for key in entities if key.startswith("character:")]
    specs = [("prepare", [])]
    if event_artwork_only:
        event_paths = sorted(set(await session.scalars(
            select(GameEvent.banner_path).where(GameEvent.banner_path.is_not(None))
        )))
        if not event_paths:
            return {"release_id": release_id, "parent_id": None, "tasks": 0,
                    "queued": 0, "enqueue_failed": 0}
        specs.extend(("event_images", batch) for batch in batches(event_paths))
    if not event_artwork_only and cutscenes:
        specs.append(("cutscene_assets", sorted(set(cutscenes))))
    if not event_artwork_only and (voices or characters):
        specs.append(("voice_packages", []))
    if not event_artwork_only:
        specs.extend(("images", batch) for batch in batches(entities))
        specs.extend(("cutscene", [name]) for name in sorted(set(cutscenes)))
        specs.extend(("voices", batch) for batch in batches(voices))
        specs.extend(("character_voices", batch) for batch in batches(characters))
    pending, runs = [], []
    parent_id = None
    dependencies = {}
    for kind, targets in specs:
        dependency = dependencies.get("cutscene_assets" if kind == "cutscene" else "voice_packages" if kind in {"voices", "character_voices"} else "prepare")
        request = {"release_id": release_id, "game_version": release.game_version,
                   "kind": kind, "targets": targets, "parent_id": dependency}
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).digest()
        run = await session.scalar(select(ProcessingRun).where(
            ProcessingRun.processor_id == processor, ProcessingRun.input_hash == digest)
            .order_by(ProcessingRun.id.desc()).limit(1))
        if run is None:
            run = ProcessingRun(processor_id=processor, input_hash=digest)
            session.add(run)
            await session.flush()
        dependencies[kind] = run.id
        if kind == "prepare":
            parent_id = run.id
        queue = EVENT_QUEUE if event_artwork_only and kind in {"prepare", "event_images"} else QUEUE
        already_queued = run.status == "queued" and (run.metadata_json or {}).get("queue", QUEUE) == queue
        if run.status not in {"running", "completed", "waiting_dependency"} and not already_queued:
            payload = {"schema_version": 1, "job_type": "media.release", "run_id": run.id,
                       **request}
            if run.status in {"partial", "failed", "blocked"}:
                run.raw_output = None
            run.status, run.error, run.finished_at = "queued", None, None
            run.metadata_json = {**(run.metadata_json or {}), "payload": payload, "request": request,
                                 "queue": queue}
            pending.append((run, payload, queue))
        runs.append(run)
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
    return {"release_id": release_id, "parent_id": parent_id, "tasks": len(runs),
            "queued": len(pending) - failed, "enqueue_failed": failed}
