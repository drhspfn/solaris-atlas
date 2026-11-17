from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wuwa_story.visual_memory.inference import VisualMemoryUnavailable

from wuwa_story_worker import video_identity


def workspace(tmp_path, count=72):
    paths = {}
    for index in range(1, count + 1):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(str(index).encode())
        paths[index] = path
    return SimpleNamespace(
        frame_paths=paths,
        frame_times={index: (index - 1) * 3 for index in paths},
        atlases=[SimpleNamespace(atlas_index=index + 1, frame_number_start=index * 12 + 1,
                                 frame_number_end=index * 12 + 12)
                 for index in range(count // 12)],
    )


@pytest.mark.asyncio
async def test_identification_uses_supported_service_and_retains_evidence_and_uncertainty(tmp_path, monkeypatch):
    detections = [{"bbox": [2, 3, 8, 9], "detection": {"score": 0.9, "status": "detected"},
                   "identification": {"status": "unresolved", "score_type": "uncalibrated_similarity",
                                      "candidates": [{"name": "Candidate", "evidence": [{"reference_id": 7}]}]}}]
    service = SimpleNamespace(identify=AsyncMock(return_value={"model_key": "model", "detections": detections}))
    monkeypatch.setattr(video_identity, "VisualMemoryService", lambda db: service)
    result = await video_identity.identify_atlas_characters(object(), workspace(tmp_path),
                                                            global_frame_indices=[58])
    service.identify.assert_awaited_once_with(b"58", detect=True, limit=3)
    assert result["available"] and result["inspected_frames"] == [58]
    assert result["matches"] == [{"global_frame": 58, "time": 171, "model_key": "model",
                                  "detections": detections}]
    assert result["matches"][0]["detections"][0]["identification"]["status"] == "unresolved"


@pytest.mark.asyncio
async def test_atlas_sampling_covers_later_scenes_and_prioritizes_explicit_frames(tmp_path, monkeypatch):
    service = SimpleNamespace(identify=AsyncMock(return_value={"detections": []}))
    monkeypatch.setattr(video_identity, "VisualMemoryService", lambda db: service)
    result = await video_identity.identify_atlas_characters(
        object(), workspace(tmp_path), atlas_indices=[1, 2, 3, 4, 5, 6], global_frame_indices=[58, 999]
    )
    assert result["truncated"] and len(result["inspected_frames"]) == 16
    assert result["inspected_frames"][0] == 58
    assert 1 in result["inspected_frames"] and 72 in result["inspected_frames"]
    assert result["missing_frames"] == [999]
    assert len({match["global_frame"] for match in result["matches"]}) == 16


@pytest.mark.asyncio
async def test_unavailable_models_and_invalid_selection_do_not_invent_results(tmp_path, monkeypatch):
    service = SimpleNamespace(identify=AsyncMock(side_effect=VisualMemoryUnavailable("missing model")))
    monkeypatch.setattr(video_identity, "VisualMemoryService", lambda db: service)
    frames = workspace(tmp_path)
    unavailable = await video_identity.identify_atlas_characters(object(), frames, global_frame_indices=[1])
    assert unavailable["reason"] == "visual_memory_models_unavailable"
    assert not unavailable["available"] and unavailable["matches"] == []
    invalid = await video_identity.identify_atlas_characters(object(), frames, global_frame_indices=[999])
    assert invalid["reason"] == "no_matching_frames" and invalid["missing_frames"] == [999]
    assert service.identify.await_count == 1
