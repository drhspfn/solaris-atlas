from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.agents import scene_analysis_reader
from wuwa_story.agents.contracts import CutsceneDescription
from wuwa_story.api.routes import cutscene_analysis
from wuwa_story.story import cutscene_descriptions


@pytest.mark.asyncio
async def test_publish_single_full_logical_scene_against_selected_video(monkeypatch):
    report = SimpleNamespace(id=12, owner_node_id=500, metadata_json={"asset_version": "3.8.0"})
    file = SimpleNamespace(sha256=b"test")
    video = SimpleNamespace(file_id=40, metadata_json={"duration_seconds": 10})
    plan = {"segments": [{"asset_node_id": 100, "source_start": 0, "source_end": 10,
                          "source_sha256": file.sha256.hex()}]}
    session = SimpleNamespace(get=AsyncMock(side_effect=[report, file]))
    monkeypatch.setattr(cutscene_descriptions, "current_video", AsyncMock(return_value=video))
    monkeypatch.setattr(cutscene_analysis, "_logical_plan", AsyncMock(return_value=(plan, video, {})))
    monkeypatch.setattr(scene_analysis_reader, "current_scene_report", AsyncMock(return_value=report))
    save = AsyncMock()
    monkeypatch.setattr(cutscene_descriptions, "save_description", save)
    entry = CutsceneDescription.model_validate({"asset_node_id": 100, "visual_reference_id": 12,
        "title": "The meeting", "text": "A meeting", "observation_indices": [0]})
    await cutscene_descriptions.publish_descriptions(session, [entry], release_id=7, locale_id=1,
                                                     source_document_id=8, processor_run_id=9)
    assert save.await_args.kwargs["source_hash"] == b"test"
    plan["segments"][0]["source_start"] = 1
    session.get = AsyncMock(side_effect=[report, file])
    with pytest.raises(ValueError, match="partial video"):
        await cutscene_descriptions.publish_descriptions(session, [entry], release_id=7, locale_id=1,
                                                         source_document_id=8, processor_run_id=9)
