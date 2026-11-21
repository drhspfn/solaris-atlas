"""Read-only visual-memory identification for numbered video frames."""

import asyncio
import hashlib
import io
from typing import Any

from sqlalchemy import or_, select
from wuwa_story.db.models.agent_workspace import AgentFactBoard
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.visual_memory import (
    VisualAppearance,
    VisualIdentity,
    VisualIdentityNode,
    VisualReferenceLink,
)
from wuwa_story.visual_memory.images import load_image
from wuwa_story.visual_memory.inference import VisualMemoryUnavailable
from wuwa_story.visual_memory.service import VisualMemoryService

MAX_IDENTITY_FRAMES = 16


async def character_reference_image(db: Any, args: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    """Read a bounded published image or approved visual reference, never an arbitrary URL."""
    asset_id, reference_id = args.get("asset_id"), args.get("reference_id")
    # Strict-schema models frequently pass 0 for omitted optional integer fields.
    if asset_id == 0:
        asset_id = None
    if reference_id == 0:
        reference_id = None
    if (asset_id is None) == (reference_id is None):
        return {
            "error": "provide_exactly_one_id",
            "hint": "Pass only reference_id (from identify_atlas_characters reference_ids) OR only asset_id; omit the other field instead of passing both.",
        }, None
    selected = asset_id if asset_id is not None else reference_id
    if type(selected) is not int or selected <= 0:
        return {"error": "invalid_id"}, None
    if db is None:
        return {"error": "database_unavailable"}, None
    service = VisualMemoryService(db)
    try:
        reference = None
        if reference_id is not None:
            reference = await service._reference(reference_id)
            if reference.status != "approved" or not reference.enabled_for_search:
                return {"error": "reference_not_approved"}, None
            data = await service.get_image_bytes(reference_id, max_bytes=5 * 1024 * 1024)
        else:
            data = await service.get_asset_image_bytes(asset_id)
        image = await asyncio.to_thread(load_image, data, max_bytes=5 * 1024 * 1024)
        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        encoded = buffer.getvalue()
    except (ValueError, OSError):
        return {"error": "reference_image_unavailable"}, None
    out: dict[str, Any] = {
        "asset_id": asset_id,
        "reference_id": reference_id,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "mime_type": "image/jpeg",
    }
    if reference is not None:
        src_type = getattr(reference, "source_type", None)
        if src_type:
            out["source_type"] = src_type
        src_page = getattr(reference, "source_page", None)
        if src_page:
            out["source_page"] = src_page
        if hasattr(db, "execute"):
            try:
                link_rows = (
                    await db.execute(
                        select(
                            VisualIdentity.id.label("identity_id"),
                            VisualIdentity.name.label("identity_name"),
                            VisualAppearance.name.label("appearance_name"),
                        )
                        .join(VisualIdentity, VisualIdentity.id == VisualReferenceLink.identity_id)
                        .outerjoin(VisualAppearance, VisualAppearance.id == VisualReferenceLink.appearance_id)
                        .where(VisualReferenceLink.reference_id == reference_id)
                    )
                ).mappings().all()
                if link_rows:
                    first = link_rows[0]
                    iid = int(first["identity_id"])
                    out["identity_id"] = iid
                    out["identity_name"] = first["identity_name"]
                    if first["appearance_name"]:
                        out["appearance_name"] = first["appearance_name"]
                    node_rows = (
                        await db.execute(
                            select(
                                Node.id.label("node_id"),
                                NodeType.key.label("node_type"),
                                Node.canonical_key,
                            )
                            .join(Node, Node.id == VisualIdentityNode.node_id)
                            .join(NodeType, NodeType.id == Node.type_id)
                            .where(VisualIdentityNode.identity_id == iid)
                        )
                    ).mappings().all()
                    node_ids = [int(r["node_id"]) for r in node_rows]
                    if node_rows:
                        out["linked_nodes"] = [
                            {
                                "node_id": int(r["node_id"]),
                                "node_type": r["node_type"],
                                "canonical_key": r["canonical_key"],
                            }
                            for r in node_rows
                        ]
                    if node_ids and hasattr(db, "scalars"):
                        fact_rows = list(
                            await db.scalars(
                                select(AgentFactBoard)
                                .where(
                                    AgentFactBoard.status == "active",
                                    or_(
                                        AgentFactBoard.subject_node_id.in_(node_ids),
                                        AgentFactBoard.object_node_id.in_(node_ids),
                                    ),
                                )
                                .order_by(AgentFactBoard.importance.desc(), AgentFactBoard.updated_at.desc())
                                .limit(5)
                            )
                        )
                        if fact_rows:
                            out["facts"] = [
                                f"{f.title}: {f.description[:180]}".strip()
                                for f in fact_rows
                            ]
            except Exception:
                pass
    return out, encoded


def _compact_detection(detection: dict[str, Any]) -> dict[str, Any]:
    identification = detection.get("identification") or {}
    candidates = []
    for candidate in identification.get("candidates", []):
        entry = {key: candidate[key] for key in ("identity_id", "name", "kind", "lore_id", "appearance_id",
                                                   "score", "best_similarity", "independent_references")
                 if key in candidate}
        entry["reference_ids"] = [item["reference_id"] for item in candidate.get("evidence", [])
                                  if "reference_id" in item][:3]
        candidates.append(entry)
    return {"bbox": detection.get("bbox"), "detection": detection.get("detection"),
            "identification": {"status": identification.get("status"),
                               "score_type": identification.get("score_type"), "candidates": candidates}}


async def _enrich_match_candidates(db: Any, matches: list[dict[str, Any]]) -> None:
    """Attach linked graph nodes and fact-board summaries to visual candidates."""
    identity_ids = sorted({
        int(cand["identity_id"])
        for match in matches
        for det in match.get("detections", [])
        for cand in (det.get("identification") or {}).get("candidates", [])
        if isinstance(cand.get("identity_id"), int)
    })
    if not identity_ids or not hasattr(db, "execute"):
        return
    try:
        node_rows = (
            await db.execute(
                select(
                    VisualIdentityNode.identity_id,
                    Node.id.label("node_id"),
                    NodeType.key.label("node_type"),
                    Node.canonical_key,
                )
                .join(Node, Node.id == VisualIdentityNode.node_id)
                .join(NodeType, NodeType.id == Node.type_id)
                .where(VisualIdentityNode.identity_id.in_(identity_ids))
            )
        ).mappings().all()
    except Exception:
        return
    nodes_by_identity: dict[int, list[dict[str, Any]]] = {}
    node_ids: set[int] = set()
    for row in node_rows:
        nid = int(row["node_id"])
        node_ids.add(nid)
        nodes_by_identity.setdefault(int(row["identity_id"]), []).append({
            "node_id": nid,
            "node_type": row["node_type"],
            "canonical_key": row["canonical_key"],
        })
    facts_by_node: dict[int, list[str]] = {}
    if node_ids:
        try:
            fact_rows = list(
                await db.scalars(
                    select(AgentFactBoard)
                    .where(
                        AgentFactBoard.status == "active",
                        or_(
                            AgentFactBoard.subject_node_id.in_(node_ids),
                            AgentFactBoard.object_node_id.in_(node_ids),
                        ),
                    )
                    .order_by(AgentFactBoard.importance.desc(), AgentFactBoard.updated_at.desc())
                    .limit(40)
                )
            )
        except Exception:
            fact_rows = []
        for fact in fact_rows:
            snippet = f"{fact.title}: {fact.description[:180]}".strip()
            for nid in (fact.subject_node_id, fact.object_node_id):
                if nid in node_ids:
                    bucket = facts_by_node.setdefault(nid, [])
                    if snippet not in bucket and len(bucket) < 3:
                        bucket.append(snippet)
    for match in matches:
        for det in match.get("detections", []):
            ident = det.get("identification") or {}
            status = ident.get("status")
            for cand in ident.get("candidates", []):
                iid = cand.get("identity_id")
                linked = nodes_by_identity.get(iid, []) if isinstance(iid, int) else []
                if linked:
                    cand["linked_nodes"] = linked
                facts: list[str] = []
                for item in linked:
                    for snippet in facts_by_node.get(item["node_id"], []):
                        if snippet not in facts and len(facts) < 2:
                            facts.append(snippet)
                if facts:
                    cand["summary_facts"] = facts
                best_sim = cand.get("best_similarity") or cand.get("score") or 0.0
                if status == "unknown" and best_sim >= 0.72 and cand.get("reference_ids"):
                    cand["verify_visually"] = True


def _spread(frames: list[int], count: int) -> list[int]:
    if count <= 0:
        return []
    if len(frames) <= count:
        return frames
    if count == 1:
        return [frames[len(frames) // 2]]
    return [frames[i * (len(frames) - 1) // (count - 1)] for i in range(count)]


async def identify_atlas_characters(
    db: Any,
    workspace: Any,
    atlas_indices: list[int] | None = None,
    global_frame_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Use the same detector, model fingerprint and confidence policy as visual_identify."""
    explicit = list(dict.fromkeys(global_frame_indices or []))
    missing = [index for index in explicit if index not in workspace.frame_paths]
    explicit = [index for index in explicit if index in workspace.frame_paths]
    wanted = set(atlas_indices or [])
    atlas_frames = sorted({
        frame
        for atlas in workspace.atlases if atlas.atlas_index in wanted
        for frame in range(atlas.frame_number_start, atlas.frame_number_end + 1)
        if frame in workspace.frame_paths and frame not in explicit
    })
    all_count = len(explicit) + len(atlas_frames)
    selected = explicit[:MAX_IDENTITY_FRAMES]
    selected += _spread(atlas_frames, MAX_IDENTITY_FRAMES - len(selected))
    base = {"inspected_frames": [], "requested_frames": selected,
            "missing_frames": missing, "truncated": all_count > len(selected), "matches": []}
    if not selected:
        return {**base, "available": False, "reason": "no_matching_frames"}
    if db is None:
        return {**base, "available": False, "reason": "database_unavailable_in_local_simulation"}
    service = VisualMemoryService(db)
    for index in selected:
        path = workspace.frame_paths[index]
        if not path.is_file():
            base["missing_frames"].append(index)
            continue
        image = await asyncio.to_thread(path.read_bytes)
        try:
            result = await service.identify(image, detect=True, limit=3)
        except VisualMemoryUnavailable:
            return {**base, "available": False, "reason": "visual_memory_models_unavailable"}
        base["inspected_frames"].append(index)
        base["matches"].append({
            "global_frame": index, "time": workspace.frame_times.get(index),
            "model_key": result.get("model_key"),
            "detections": [_compact_detection(item) for item in result.get("detections", [])],
        })
    await _enrich_match_candidates(db, base["matches"])
    return {**base, "available": True}
