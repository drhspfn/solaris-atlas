"""Read-only visual-memory identification for numbered video frames."""

import asyncio
from typing import Any

from wuwa_story.visual_memory.inference import VisualMemoryUnavailable
from wuwa_story.visual_memory.service import VisualMemoryService

MAX_IDENTITY_FRAMES = 16


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
            "model_key": result.get("model_key"), "detections": result.get("detections", []),
        })
    return {**base, "available": True}
