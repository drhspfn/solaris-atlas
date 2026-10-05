from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from wuwa_story.api.routes.cutscene_analysis import VisualRequest, create_visual_jobs, public_job


@pytest.mark.asyncio
async def test_disabled_visual_queue_rejects_before_database_work():
    db = AsyncMock()
    with patch(
        "wuwa_story.api.routes.cutscene_analysis.get_agent_settings",
        return_value=SimpleNamespace(vision_enabled=False),
    ):
        with pytest.raises(HTTPException) as error:
            await create_visual_jobs(VisualRequest(asset_node_ids=[1], game_version="3.7.0"), db)
        assert error.value.status_code == 409
        db.scalars.assert_not_called()


@pytest.mark.asyncio
async def test_missing_variant_does_not_enqueue_partial_scene():
    db = AsyncMock()
    db.scalars.return_value = [SimpleNamespace(owner_node_id=1)]
    with (
        patch(
            "wuwa_story.api.routes.cutscene_analysis.get_agent_settings",
            return_value=SimpleNamespace(vision_enabled=True),
        ),
        patch(
            "wuwa_story.api.routes.cutscene_analysis.enqueue_visual_job", new_callable=AsyncMock
        ) as enqueue,
    ):
        with pytest.raises(HTTPException) as error:
            await create_visual_jobs(VisualRequest(asset_node_ids=[1, 2], game_version="3.7.0"), db)
        assert error.value.status_code == 404
        enqueue.assert_not_called()
        db.commit.assert_not_called()


def run():
    return SimpleNamespace(
        id=9,
        target_node_id=1,
        status="pending",
        metadata_json={"asset_version": "3.7.0"},
        raw_output={"step": 2, "batches": [{"events": [{}, {}]}], "pending": [[3, 6], [9]]},
        error=None,
        tokens_input=100,
        tokens_output=30,
        cost=0.01,
    )


@pytest.mark.asyncio
async def test_duplicate_asset_ids_enqueue_once_and_commit():
    db = AsyncMock()
    db.scalars.return_value = [SimpleNamespace(owner_node_id=1)]
    with (
        patch(
            "wuwa_story.api.routes.cutscene_analysis.get_agent_settings",
            return_value=SimpleNamespace(vision_enabled=True),
        ),
        patch(
            "wuwa_story.api.routes.cutscene_analysis.enqueue_visual_job",
            new_callable=AsyncMock,
            return_value=run(),
        ) as enqueue,
    ):
        result = await create_visual_jobs(
            VisualRequest(asset_node_ids=[1, 1], game_version="3.7.0"), db
        )
        assert result["jobs"][0]["id"] == 9
        enqueue.assert_awaited_once()
        db.commit.assert_awaited_once()


def test_public_status_contains_progress_without_raw_prompts():
    status = public_job(run())
    assert status["frames_done"] == 2 and status["frames_remaining"] == 3
    assert "settings" not in status and "raw_output" not in status
