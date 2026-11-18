"""Read-only visual-memory identification for numbered video frames."""

import asyncio
import hashlib
import io
from typing import Any

from wuwa_story.visual_memory.images import load_image
from wuwa_story.visual_memory.inference import VisualMemoryUnavailable
from wuwa_story.visual_memory.service import VisualMemoryService

MAX_IDENTITY_FRAMES = 16


async def character_reference_image(db: Any, args: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    """Read a bounded published image or approved visual reference, never an arbitrary URL."""
    asset_id, reference_id = args.get("asset_id"), args.get("reference_id")
    if (asset_id is None) == (reference_id is None):
        return {"error": "provide_exactly_one_id"}, None
    selected = asset_id if asset_id is not None else reference_id
    if type(selected) is not int or selected <= 0:
        return {"error": "invalid_id"}, None
    if db is None:
        return {"error": "database_unavailable"}, None
    service = VisualMemoryService(db)
    try:
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
    return {"asset_id": asset_id, "reference_id": reference_id,
            "sha256": hashlib.sha256(encoded).hexdigest(), "mime_type": "image/jpeg"}, encoded


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
    return {**base, "available": True}
