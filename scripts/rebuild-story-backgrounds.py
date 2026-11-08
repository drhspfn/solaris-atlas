#!/usr/bin/env python3
"""Bounded legacy illustrated-story rebuild using the normal durable media queue.

Dry run by default. Replaces references only through successful normal publication;
never deletes source archives or clears queues. Run inside the new image worker.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import String, or_, select, text
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.ops import GameRelease, ProcessingRun, Processor
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.session import SessionFactory, engine
from wuwa_story.ingestion.entity_media import MediaRequest, entity_media_targets
from wuwa_story.ingestion.media_jobs import enqueue_entity_media
from wuwa_story.ingestion.media_workspaces import workspace_expired
from wuwa_story.ingestion.spine_presentation import SPINE_RECIPE


def digest(request, targets):
    payload = {"schema_version": 1, "job_type": "media.entities",
               "request": request.model_dump(), "targets": targets,
               "execution_queue": "wuwa.illustrated-backgrounds.v1"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


async def run(args):
    if args.apply and os.getenv("WUWA_DB_TRANSACTION_POOLING") == "1":
        raise ValueError("Coordinator requires a direct database connection for session locks")
    report = {"apply": args.apply, "recipe": SPINE_RECIPE, "results": []}
    try:
        if SPINE_RECIPE != args.expected_recipe:
            raise ValueError("Deployed worker/server recipe does not match the approved rollout")
        async with engine.connect() as lock:
            if not await lock.scalar(text("SELECT pg_try_advisory_lock(73143, 1)")):
                raise ValueError("Another story-background coordinator is active")
            try:
                async with SessionFactory() as session:
                    prepare = await session.get(ProcessingRun, args.workspace_id)
                    client = prepare.raw_output if prepare else {}
                    if not prepare or prepare.status != "completed" or workspace_expired(prepare):
                        raise ValueError("Retained completed workspace is unavailable")
                    if client.get("workspace_id") != args.workspace_id or not client.get("download_id"):
                        raise ValueError("Preparation has no pinned client identity")
                    base = Path(os.getenv("WUWA_ASSET_WORKSPACE", "/var/lib/wuwa-worker/client-assets"))
                    root = base / "patch-workspaces" / str(args.workspace_id)
                    plan = json.loads((root / "plan.json").read_text())
                    status = json.loads((root / "status.json").read_text())
                    if (plan.get("id") != client["download_id"] or status.get("id") != plan["id"]
                            or plan.get("version") != client.get("asset_version") or plan.get("tier") != client.get("tier")
                            or status.get("state") != "downloaded" or not (root / "game").is_dir()
                            or not (root / "keys.txt").is_file()):
                        raise ValueError("Pinned client files are missing or mismatched")
                    rows = (await session.execute(select(Node.canonical_key, FileReference.release_id)
                        .join(FileReference, FileReference.owner_node_id == Node.id)
                        .where(FileReference.reference_type.in_(["story_spine", "story_presentation", "entity_image"]),
                               Node.canonical_key.like("flow_state:%"), FileReference.release_id.is_not(None),
                               or_(FileReference.reference_type != "story_presentation",
                                   FileReference.metadata_json["game_source"]["source_files"].cast(String).like("%/UiCG/%"),
                                   FileReference.metadata_json["sequence_media"]["composition"]["state"].astext == "ready"))
                        .distinct())).all()
                    priority = {owner: i for i, owner in enumerate(args.priority)}
                    rows = sorted(rows, key=lambda row: (row[0] not in priority, -row[1], priority.get(row[0], len(priority)), row[0]))
                    if args.owner:
                        rows = [row for row in rows if row[0] in args.owner]
                    submitted = 0
                    for owner, release_id in rows:
                        if submitted >= args.limit:
                            break
                        release = await session.get(GameRelease, release_id)
                        request = MediaRequest(download_id=client["download_id"], asset_version=client["asset_version"],
                            tier=client["tier"], workspace_id=args.workspace_id, release_id=release_id,
                            game_version=release.game_version, entities=[owner], kinds=["image"],
                            voice_plan_id=client.get("voice_plan_id"))
                        try:
                            targets = await entity_media_targets(session, request)
                        except ValueError as error:
                            report["results"].append({"owner": owner, "release_id": release_id,
                                "action": "hold_target_resolution", "reason": str(error)[:200]})
                            save(args.report, report)
                            continue
                        identity = digest(request, targets)
                        previous = await session.scalar(select(ProcessingRun).join(Processor)
                            .where(Processor.key == "entity_media", ProcessingRun.input_hash == identity)
                            .order_by(ProcessingRun.id.desc()).limit(1))
                        result = {"owner": owner, "release_id": release_id, "input_hash": identity.hex()}
                        if previous and (previous.status in {"running", "queued", "waiting_dependency", "completed"}
                                         or (previous.status in {"partial", "failed"} and not args.retry_failed)):
                            result.update(action="existing", run_id=previous.id, status=previous.status)
                        elif args.apply:
                            # Persist the planned identity before contacting RabbitMQ. On uncertain
                            # dispatch reruns consult the same durable ProcessingRun identity.
                            result["action"] = "dispatching"
                            report["results"].append(result)
                            save(args.report, report)
                            job = await enqueue_entity_media(session, request, retry_partial=True, execution_queue="wuwa.illustrated-backgrounds.v1")
                            result.update(action="submitted", run_id=job.id, status=job.status, input_hash=job.input_hash.hex())
                            submitted += 1
                            save(args.report, report)
                            continue
                        else:
                            result["action"] = "would_enqueue"
                            submitted += 1
                        report["results"].append(result)
                    report["new_jobs"] = submitted
                    save(args.report, report)
            finally:
                await lock.execute(text("SELECT pg_advisory_unlock(73143, 1)"))
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=int, required=True)
    parser.add_argument("--expected-recipe", type=int, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--owner", action="append", default=[])
    parser.add_argument("--priority", action="append", default=[])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="Retry a reviewed failed/partial job; requires an explicit --owner")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500 or args.workspace_id < 1:
        parser.error("limit must be 1..500 and workspace-id positive")
    if args.retry_failed and not args.owner:
        parser.error("--retry-failed requires an explicit reviewed --owner")
    asyncio.run(run(args))
