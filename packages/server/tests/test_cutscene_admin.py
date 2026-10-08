import hashlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.api.routes.cutscene_analysis import VisualRequest, create_visual_jobs, public_job


@pytest.mark.asyncio
async def test_preview_uses_exact_version_and_separate_audio():
    from wuwa_story.api.routes.cutscene_analysis import playback
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(canonical_key="asset:ue:/scene")
    with (
        patch("wuwa_story.api.routes.cutscene_analysis.cutscene_videos", new_callable=AsyncMock,
              return_value={7: {"url": "https://cdn/video", "asset_version": "3.7.0"}}) as videos,
        patch("wuwa_story.api.routes.cutscene_analysis.audio_bundles", new_callable=AsyncMock,
              return_value={7: {"videos": {"full": "https://cdn/silent"}, "tracks": [{"role": "music"}]}}),
    ):
        result = await playback(7, "3.7.0", db)
    videos.assert_awaited_once_with(db, [7], "3.7.0")
    assert result["media"]["asset:ue:/scene"]["url"] == "https://cdn/silent"
    assert result["media"]["asset:ue:/scene"]["audio_tracks"] == [{"role": "music"}]


@pytest.mark.asyncio
async def test_missing_preview_returns_404():
    from wuwa_story.api.routes.cutscene_analysis import playback
    with patch("wuwa_story.api.routes.cutscene_analysis.cutscene_videos", new_callable=AsyncMock, return_value={}):
        with pytest.raises(HTTPException) as error:
            await playback(7, "1.0.0", AsyncMock())
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_http_actions_enforce_admin_and_csrf():
    import httpx
    from fastapi import FastAPI

    from wuwa_story.api.routes import story_agent
    from wuwa_story.auth.constants import UserRole
    from wuwa_story.auth.dependencies import get_auth_context
    from wuwa_story.db.session import get_session

    app = FastAPI()
    app.include_router(story_agent.admin)

    async def database():
        yield AsyncMock()

    role = UserRole.USER

    async def auth():
        return SimpleNamespace(user=SimpleNamespace(role=role))

    app.dependency_overrides[get_session] = database
    app.dependency_overrides[get_auth_context] = auth
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ["/admin/story-agent/cutscenes/jobs", "/admin/story-agent/cutscenes/assets"]:
            assert (await client.get(path)).status_code == 403
        payload = {"asset_node_ids": [1], "game_version": "3.7.0"}
        assert (
            await client.post("/admin/story-agent/cutscenes/jobs", json=payload)
        ).status_code == 403
        role = UserRole.ADMIN
        response = await client.post("/admin/story-agent/cutscenes/jobs", json=payload)
        assert response.status_code == 403 and response.json()["detail"]["code"] == "CSRF_INVALID"
        assert (
            await client.post("/admin/story-agent/cutscenes/jobs/1/resume", json={})
        ).status_code == 403


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


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Isolated PostgreSQL required")
async def test_postgres_assets_select_latest_versioned_bytes_and_filtered_jobs():
    from wuwa_story.api.routes.cutscene_analysis import assets, visual_jobs
    from wuwa_story.db.models.graph import Node, NodeType
    from wuwa_story.db.models.storage import FileObject, FileReference, FileType

    engine = create_async_engine(os.environ["WUWA_TEST_DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(connection, expire_on_commit=False) as db:
                suffix = uuid4().hex
                node = Node(
                    type_id=await db.scalar(
                        select(NodeType.id).where(NodeType.key == "asset_reference")
                    ),
                    canonical_key="admin-vision:" + suffix,
                )
                db.add(node)
                await db.flush()
                file = FileObject(
                    file_type_id=await db.scalar(select(FileType.id).limit(1)),
                    sha256=hashlib.sha256(suffix.encode()).digest(),
                )
                db.add(file)
                await db.flush()
                for version in ["3.6.0", "3.7.0", "3.7.0"]:
                    db.add(
                        FileReference(
                            owner_node_id=node.id,
                            file_id=file.id,
                            reference_type="cutscene_video",
                            metadata_json={"asset_version": version},
                        )
                    )
                    await db.flush()
                result = await assets(q=suffix, before=None, limit=30, session=db)
                assert len(result["assets"]) == 2
                assert {item["game_version"] for item in result["assets"]} == {"3.6.0", "3.7.0"}
                assert await visual_jobs(before=None, run_id=-1, limit=30, session=db) == {
                    "jobs": [],
                    "next_before": None,
                }
            await transaction.rollback()
    finally:
        await engine.dispose()
