import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
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
async def test_agent_tool_dispatch_reaches_shared_memory_service(tmp_path, monkeypatch):
    from wuwa_story.agents.providers import ToolCall

    from wuwa_story_worker.video_analysis_agent import _execute_video_tool

    service = SimpleNamespace(identify=AsyncMock(return_value={"model_key": "model", "detections": []}))
    monkeypatch.setattr(video_identity, "VisualMemoryService", lambda db: service)
    output, atlases = await _execute_video_tool(
        object(), workspace(tmp_path),
        ToolCall("identity-58", "identify_atlas_characters", {"global_frame_indices": [58]}),
    )
    service.identify.assert_awaited_once_with(b"58", detect=True, limit=3)
    assert output["available"] and output["inspected_frames"] == [58]
    assert atlases == []


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
    candidate = result["matches"][0]["detections"][0]["identification"]["candidates"][0]
    assert candidate == {"name": "Candidate", "reference_ids": [7]}
    assert result["matches"][0]["global_frame"] == 58
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


@pytest.mark.asyncio
async def test_unknown_detection_is_available_and_keeps_its_status(tmp_path, monkeypatch):
    detection = {"bbox": [0, 0, 2, 2], "identification": {"status": "unknown", "candidates": []}}
    service = SimpleNamespace(identify=AsyncMock(return_value={"detections": [detection]}))
    monkeypatch.setattr(video_identity, "VisualMemoryService", lambda db: service)
    result = await video_identity.identify_atlas_characters(object(), workspace(tmp_path),
                                                            global_frame_indices=[1])
    assert result["available"] is True
    assert result["matches"][0]["detections"][0]["identification"]["status"] == "unknown"
    assert "reason" not in result


@pytest.mark.asyncio
async def test_character_reference_uses_only_approved_or_published_images(monkeypatch):
    data = io.BytesIO()
    Image.new("RGB", (12, 16), "red").save(data, format="PNG")
    service = SimpleNamespace(_reference=AsyncMock(return_value=SimpleNamespace(status="approved", enabled_for_search=True)),
                              get_image_bytes=AsyncMock(return_value=data.getvalue()),
                              get_asset_image_bytes=AsyncMock(return_value=data.getvalue()))
    monkeypatch.setattr(video_identity, "VisualMemoryService", lambda db: service)
    result, image = await video_identity.character_reference_image(object(), {"reference_id": 7})
    assert result["reference_id"] == 7 and len(result["sha256"]) == 64
    assert Image.open(io.BytesIO(image)).format == "JPEG"
    service._reference.return_value.status = "pending"
    assert (await video_identity.character_reference_image(object(), {"reference_id": 7}))[0]["error"] == "reference_not_approved"
    assert (await video_identity.character_reference_image(object(), {"asset_id": 5}))[1] is not None
    assert (await video_identity.character_reference_image(object(), {"asset_id": 0, "reference_id": 7}))[0]["error"] == "reference_not_approved"
    assert (await video_identity.character_reference_image(object(), {"asset_id": 5, "reference_id": 0}))[1] is not None
    assert (await video_identity.character_reference_image(object(), {"asset_id": 5, "reference_id": 7}))[0]["error"] == "provide_exactly_one_id"
