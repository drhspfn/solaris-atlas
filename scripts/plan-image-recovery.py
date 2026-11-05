"""Read-only image recovery inventory; never enqueue or replay saved payloads.

Run in the server Python environment with its configured DB connection:
    python scripts/plan-image-recovery.py --run-id 25117 --run-id 25126 --output plan.json

Use the deployed resolver after the deployment gate. The JSON retains old/current
targets and current requests for review. Coverage is DB evidence only: no storage
HEAD, tool fingerprint verification, or extraction is performed. A later executor
must revalidate this manifest and skip completed/current active identities BEFORE
calling enqueue_entity_media(session, current_request, retry_partial=True).
"""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.ingestion import entity_media
from wuwa_story.ingestion.character_artwork import (
    character_artwork_scope,
    inherited_character_fields,
)
from wuwa_story.ingestion.entity_media import MediaRequest, entity_media_targets, image_paths
from wuwa_story.ingestion.media_provenance import matches_source, target_identity

ACTIVE = {"pending", "queued", "running", "waiting_dependency"}


def payload_for(request, targets):
    return {"schema_version": 1, "job_type": "media.entities",
            "request": request.model_dump(), "targets": targets}


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def compare_targets(old, current):
    before = {target_identity(t): t for t in old}
    after = {target_identity(t): t for t in current}
    return {
        "removed": [t for k, t in before.items() if k not in after],
        "added": [t for k, t in after.items() if k not in before],
        "changed": [{"old": before[k], "current": t}
                    for k, t in after.items() if k in before and before[k] != t],
    }


def action_for(original_status, current_status, targets):
    if original_status == "completed":
        return "skip_completed"
    if original_status in ACTIVE:
        return "leave_original_delivery"
    if original_status == "cancelled" or current_status == "cancelled":
        return "hold_cancelled"
    if current_status == "completed":
        return "skip_completed"
    if current_status in ACTIVE:
        return "reuse_current_delivery"
    if not targets:
        return "no_current_authored_targets"
    return "review_then_normal_enqueue_current_request"


def removal_reason(target, raw, template, related_paths):
    role_id = raw.get("Id")
    inherited = inherited_character_fields(raw, template)
    fields = [field for field, value in raw.items() if value == target["path"]]
    if any(field in inherited for field in fields):
        return "inherited_foreign_role_field"
    if any(character_artwork_scope(field, target["path"], role_id) == "shared_resource"
           for field in fields):
        return "shared_or_placeholder_artwork_filter"
    if inherited and target["path"] in related_paths:
        return "inherited_foreign_role_skill_tree_resonance_filter"
    return "unexplained_source_or_resolver_change"


async def rows(session, release_id, path, field, value):
    return list(await session.scalars(select(SourceRecord.data).join(SourceFile).where(
        SourceRecord.release_id == release_id, SourceFile.logical_source_path == path,
        SourceRecord.data[field].as_integer() == value,
    ).order_by(SourceRecord.row_index)))


async def explain_removed(session, request, removed):
    reasons = []
    for owner in sorted({t["owner"] for t in removed}):
        node = await session.get(Node, owner)
        raw = await session.scalar(select(SourceRecord.data).join(
            NodeRevision, NodeRevision.source_record_id == SourceRecord.id,
        ).where(NodeRevision.node_id == owner, NodeRevision.release_id == request.release_id)
            .order_by(NodeRevision.revision.desc()).limit(1))
        raw = raw or {}
        template, related = {}, []
        if node and node.canonical_key.startswith("character:"):
            templates = await rows(session, request.release_id, "BinData/role/roleinfo.json",
                                   "Id", raw.get("SkillId"))
            template = templates[0] if templates else {}
            for path, field, value in (
                ("BinData/skill/skill.json", "SkillGroupId", raw.get("SkillId") or raw.get("Id")),
                ("BinData/skillTree/skilltree.json", "NodeGroup", raw.get("SkillTreeGroupId") or raw.get("Id")),
                ("BinData/resonate_chain/resonantchain.json", "GroupId", raw.get("ResonantChainGroupId") or raw.get("Id")),
            ):
                related.extend(await rows(session, request.release_id, path, field, value))
        related_paths = set().union(*(image_paths(row) for row in related))
        for target in (t for t in removed if t["owner"] == owner):
            reasons.append({"target": target, "owner_key": node.canonical_key if node else None,
                            "reason": removal_reason(target, raw, template, related_paths),
                            "role_type": raw.get("RoleType"), "skill_template": raw.get("SkillId")})
    return reasons


async def coverage(session, request, targets):
    """Conservative same-release DB evidence; never update reference/location rows."""
    if not targets:
        return []
    result = await session.execute(select(FileReference, FileObject, FileLocation).join(
        FileObject, FileObject.id == FileReference.file_id,
    ).outerjoin(FileLocation, FileLocation.file_id == FileObject.id).where(
        FileReference.owner_node_id.in_({t["owner"] for t in targets}),
        FileReference.source_path.in_({t["path"] for t in targets}),
        FileReference.release_id == request.release_id,
    ))
    by_identity = {}
    for ref, file, location in result:
        key = (ref.owner_node_id, ref.release_id, ref.source_path, ref.source_name, ref.reference_type)
        by_identity.setdefault(key, []).append((ref, file, location))
    source = {"asset_version": request.asset_version, "download_id": request.download_id,
              "tier": request.tier, "voice_plan_id": request.voice_plan_id}
    output = []
    for target in targets:
        candidates = by_identity.get(target_identity(target), [])
        matching = [(ref, file, loc) for ref, file, loc in candidates
                    if matches_source(ref.metadata_json or {}, source, target)]
        available = [(ref, file, loc) for ref, file, loc in matching if loc and loc.available]
        state = ("db_candidate_unverified" if available else "missing_or_unavailable"
                 if matching else "stale_provenance" if candidates else "no_reference")
        output.append({"target": target, "state": state,
                       "file_ids": sorted({file.id for _, file, _ in matching})})
    return output


async def plan_run(session, run_id):
    run = await session.get(ProcessingRun, run_id)
    if run is None:
        raise ValueError(f"Run {run_id} does not exist")
    processor = await session.get(Processor, run.processor_id)
    if processor is None or processor.key != "entity_media":
        raise ValueError("Select entity_media extraction children, not release coordinators")
    metadata = run.metadata_json or {}
    request = MediaRequest.model_validate(metadata.get("request"))
    if request.kinds != ["image"] or request.release_id is None:
        raise ValueError("Only exact-release image requests are supported")
    saved = metadata.get("payload") or {}
    old = saved.get("targets")
    if (not isinstance(old, list) or saved.get("request") != request.model_dump()
            or saved.get("run_id") != run_id):
        raise ValueError("Saved payload/request mismatch; cannot review original scope")
    saved_hash = digest({k: v for k, v in saved.items() if k != "run_id"})
    if run.input_hash is None or saved_hash != run.input_hash.hex():
        raise ValueError("Saved payload/input_hash mismatch; cannot review original scope")
    # Use the normal resolver, retaining client/release/workspace and owner scope.
    try:
        current = await entity_media_targets(session, request)
    except ValueError as error:
        if str(error) != "No authored media references for this request":
            raise
        current = []
    if max(len(old), len(current)) > 10000:
        raise ValueError("Target inventory exceeds the 10000-target per-run bound")
    payload = payload_for(request, current)
    current_hash = digest(payload)
    current_run = await session.scalar(select(ProcessingRun).where(
        ProcessingRun.processor_id == run.processor_id,
        ProcessingRun.input_hash == bytes.fromhex(current_hash),
    ).order_by(ProcessingRun.id.desc()).limit(1))
    diff = compare_targets(old, current)
    reasons = await explain_removed(session, request, diff["removed"])
    results = await coverage(session, request, current)
    return {"source_run_id": run_id, "source_status": run.status,
            "source_input_hash": run.input_hash.hex() if run.input_hash else None,
            "saved_payload_hash_valid": True,
            "current_request": request.model_dump(), "current_input_hash": current_hash,
            "current_run": {"id": current_run.id, "status": current_run.status} if current_run else None,
            "action": action_for(run.status, current_run.status if current_run else None, current),
            "old_targets": old, "current_targets": current, "diff": diff,
            "removed_reasons": reasons, "reason_counts": dict(Counter(r["reason"] for r in reasons)),
            "coverage": results, "coverage_counts": dict(Counter(r["state"] for r in results)),
            "storage_head_verified": False, "tool_hashes_verified": False}


async def main_async(args):
    from wuwa_story.db.session import SessionFactory, engine

    report = {"mode": "dry_run_only", "observed_utc": datetime.now(UTC).isoformat(),
              "resolver_sha256": hashlib.sha256(Path(entity_media.__file__).read_bytes()).hexdigest(),
              "runs": []}
    try:
        async with asyncio.timeout(args.max_seconds):
            for run_id in sorted(set(args.run_id)):
                async with SessionFactory() as session:
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                    await session.execute(text("SET LOCAL statement_timeout = '8s'"))
                    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
                    report["runs"].append(await plan_run(session, run_id))
                    await session.rollback()
    finally:
        await engine.dispose()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, action="append", required=True)
    parser.add_argument("--max-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True, help="JSON file or - for stdout")
    args = parser.parse_args()
    if not 1 <= len(set(args.run_id)) <= 20 or min(args.run_id) <= 0:
        parser.error("Select 1..20 positive exact child IDs")
    if not 1 <= args.max_seconds <= 300:
        parser.error("--max-seconds must be 1..300")
    report = asyncio.run(main_async(args))
    if str(args.output) == "-":
        print(json.dumps(report, ensure_ascii=False))
        return
    # Exclusive creation avoids replacing an operator's previously reviewed manifest.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"report": str(args.output), "runs": [
        {"id": r["source_run_id"], "action": r["action"], "old": len(r["old_targets"]),
         "current": len(r["current_targets"]), "reasons": r["reason_counts"],
         "coverage": r["coverage_counts"]} for r in report["runs"]]}))


if __name__ == "__main__":
    main()
