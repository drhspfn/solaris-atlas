import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from wuwa_story.api.routes import mcp_video_tools
from wuwa_story.api.routes import visual_memory_video as video
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.session import get_session


@pytest.fixture
def setup(monkeypatch):
    api = FastAPI()
    api.include_router(video.router)
    session = SimpleNamespace(commit=AsyncMock())
    service = SimpleNamespace(**{name: AsyncMock(return_value={"id": 7}) for name in (
        "catalog", "sources", "enqueue", "get_scan", "resolve", "mark_reviewed", "reject_all",
        "source_by_reference", "frame_bytes", "edit_bbox",
    )})
    api.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(video, "service_for", lambda _: service)
    monkeypatch.setattr(mcp_video_tools, "service_for", lambda _: service)
    return api, session, service


@pytest.mark.asyncio
async def test_authorization_bounds_and_forwarding(setup):
    api, session, service = setup
    base = "/admin/visual-memory/videos"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get(base + "/catalog")).status_code == 401
        api.dependency_overrides[require_admin] = lambda: SimpleNamespace(
            id=42)
        assert (await client.get(base + "/catalog?limit=49")).status_code == 422
        response = await client.get(base + "/sources", params={"cutscene_key": "CG_1", "game_version": "3.7"})
        assert response.status_code == 200
        service.sources.assert_awaited_once_with(
            cg_name="CG_1", game_version="3.7", locale="en")
        payload = {"asset_node_id": 12, "game_version": "3.7", "asset_version": "4.0",
                   "cutscene_key": "cutscene:CG_1", "source_reference_id": 8,
                   "source_sha256": "ab" * 32}
        assert (await client.post(base + "/scans", json=payload)).status_code == 403
        assert (await client.post(base + "/scans/7/frames/48/observations", json={"bbox": [1, 1, 2, 2]})).status_code == 403
        api.dependency_overrides[require_csrf] = lambda: None
        assert (await client.post(base + "/scans", json={**payload, "frame_batch_size": 49})).status_code == 422
        assert (await client.post(base + "/scans", json={**payload, "max_frames": 48})).status_code == 422
        assert (await client.post(base + "/scans", json={**payload, "interval_seconds": 0.1})).status_code == 422
        assert (await client.post(base + "/scans", json={**payload, "source_sha256": "bad"})).status_code == 422
        assert (await client.post(base + "/scans", json={**payload, "cutscene_key": "x" * 513})).status_code == 422
        assert (await client.post(base + "/scans", json={**payload, "url": "https://evil.invalid/"})).status_code == 422
        assert (await client.post(base + "/scans", json=payload)).status_code == 200
        service.enqueue.assert_awaited_once_with(asset_node_id=12, game_version="3.7", asset_version="4.0",
                                                 cutscene_key="cutscene:CG_1",
                                                 source_reference_id=8, source_sha256="ab" * 32,
                                                 interval_seconds=3, frame_batch_size=12, force=False)
        assert (await client.get(base + "/scans/7?frame_offset=48&frame_limit=12")).status_code == 200
        service.get_scan.assert_awaited_once_with(7, frame_offset=48, frame_limit=12)
        assert (await client.get(base + "/scans/7?frame_limit=49")).status_code == 422
        assert (await client.post(base + "/scans/7/frames/48/observations?frame_offset=48", json={"bbox": [1, 1, 2, 2]})).status_code == 200
        service.edit_bbox.assert_awaited_once_with(7, 48, (1., 1., 2., 2.), frame_offset=48, frame_limit=12)
        service.edit_bbox.reset_mock()
        assert (await client.patch(base + "/scans/7/observations/48/23/bbox", json={"bbox": [1, 1, 2, 2]})).status_code == 200
        service.edit_bbox.assert_awaited_once_with(7, 48, (1., 1., 2., 2.), detection_index=23, frame_offset=None, frame_limit=12)
        url = base + "/scans/7/observations/48/0/resolve"
        assert (await client.post(url, json={"decision": "approve"})).status_code == 422
        assert (await client.post(url, json={"decision": "reject", "links": [{"identity_id": 1}]})).status_code == 422
        assert (await client.post(url, json={"decision": "approved", "links": [{"identity_id": 1}]})).status_code == 200
        service.resolve.assert_awaited_once_with(7, 48, 0, decision="approve",
                                                 links=[{"identity_id": 1, "appearance_id": None}], actor_id=42)
        assert (await client.post(base + "/scans/7/reject-all")).status_code == 200
        assert (await client.post(base + "/scans/7/reviewed")).status_code == 200
        service.reject_all.assert_awaited_once_with(7, actor_id=42)
        service.mark_reviewed.assert_awaited_once_with(7, actor_id=42)
        assert session.commit.await_count == 6


def test_redirect_cannot_leave_configured_storage(monkeypatch):
    from wuwa_story.storage import s3

    monkeypatch.setattr(s3, "S3Storage", lambda _: SimpleNamespace(
        public_base_url="https://media.example/base"))
    assert video.validated_media_url(
        "https://media.example/base/video.mp4").endswith("video.mp4")
    for url in ("https://evil.example/base/video.mp4", "https://media.example/base/%2e%2e/private",
                "https://media.example/base/%252e%252e/private", "https://media.example/base/%5cprivate",
                "https://media.example/base2/video.mp4", "https://media.example/base/video.mp4?redirect=evil"):
        with pytest.raises(HTTPException):
            video.validated_media_url(url)


@pytest.mark.asyncio
async def test_source_preview_uses_validated_published_source(setup, monkeypatch):
    from wuwa_story.storage import s3

    api, _, service = setup
    api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=42)
    monkeypatch.setattr(s3, "S3Storage", lambda _: SimpleNamespace(
        public_base_url="https://media.example/base"))
    service.source_by_reference.return_value = {
        "url": "https://media.example/base/source.mp4"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        url = "/admin/visual-memory/videos/sources/9/video?game_version=3.7"
        response = await client.get(url, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "https://media.example/base/source.mp4"
        assert response.headers["cache-control"] == "private, no-store"
        service.source_by_reference.assert_awaited_once_with(
            9, game_version="3.7")
        service.source_by_reference.reset_mock()
        response = await client.get(url + "&asset_version=4.0&cutscene_key=cutscene%3ACG_1", follow_redirects=False)
        assert response.status_code == 307
        service.source_by_reference.assert_awaited_once_with(
            9, game_version="3.7", asset_version="4.0", cutscene_key="cutscene:CG_1")
        assert (await client.get(url + "&cutscene_key=" + "x" * 513)).status_code == 422
        service.source_by_reference.return_value = {
            "url": "https://evil.example/source.mp4"}
        assert (await client.get(url, follow_redirects=False)).status_code == 422


@pytest.mark.asyncio
async def test_optional_cutscene_context_is_omitted_when_not_supplied(setup, monkeypatch):
    from wuwa_story.storage import s3

    api, _, service = setup
    api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=42)
    api.dependency_overrides[require_csrf] = lambda: None
    monkeypatch.setattr(s3, "S3Storage", lambda _: SimpleNamespace(public_base_url="https://media.example/base"))
    service.source_by_reference.return_value = {"url": "https://media.example/base/source.mp4"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get("/admin/visual-memory/videos/sources/9/video")).status_code == 307
        service.source_by_reference.assert_awaited_once_with(9)
        assert (await client.post("/admin/visual-memory/videos/scans", json={"asset_node_id": 12})).status_code == 200
        assert "cutscene_key" not in service.enqueue.await_args.kwargs
    await mcp_video_tools.enqueue_internal({"asset_node_id": 12}, None)
    assert "cutscene_key" not in service.enqueue.await_args.kwargs


@pytest.mark.asyncio
async def test_frame_requires_admin_and_is_served_only_as_jpeg(setup):
    api, _, service = setup
    url = "/admin/visual-memory/videos/scans/7/frames/0/image"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get(url)).status_code == 401
        api.dependency_overrides[require_admin] = lambda: SimpleNamespace(
            id=42)
        service.frame_bytes.return_value = b"<script>bad</script>"
        assert (await client.get(url)).status_code == 415
        service.frame_bytes.return_value = b"\xff\xd8\xfftest"
        response = await client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["x-content-type-options"] == "nosniff"
        service.frame_bytes.assert_awaited_with(7, 0)


@pytest.mark.asyncio
async def test_public_tools_do_not_expose_pending_observations_or_offer_writes(setup):
    _, _, service = setup
    from wuwa_story.agents.capabilities import allowed, internal_definitions
    from wuwa_story.agents.chat_tools import read_definitions
    from wuwa_story.api.routes.mcp import handle_list_tools

    service.sources.return_value = {"name": "CG", "game_version": "3.7", "sources": [
        {"asset_node_id": 3, "asset_version": "3.7", "url": "https://media.example/video.mp4",
         "duration": 10, "secret": "private", "scan": {"id": 7, "reviewed": False, "frames": ["private"]}}]}
    result = await mcp_video_tools.dispatch("visual_inspect_cutscene", {"cutscene_key": "CG"}, None)
    assert result["sources"][0]["reviewed"] is False
    assert result["sources"][0]["reviewed_scan_id"] is None
    assert "private" not in str(result)
    service.sources.return_value["sources"][0]["scan"]["reviewed"] = True
    result = await mcp_video_tools.dispatch("visual_inspect_cutscene", {"cutscene_key": "CG"}, None)
    assert result["sources"][0]["reviewed_scan_id"] == 7
    assert "private" not in str(result)
    service.get_scan.return_value = {"id": 7, "status": "completed", "reviewed": False,
        "frames": [{"index": 8, "time": 24, "image_url": "https://media.example/frame.jpg",
                    "candidates": ["private"], "reviewed_by": "private"}]}
    assert "error" in await mcp_video_tools.dispatch("visual_get_video_scan", {"scan_id": 7}, None)
    service.get_scan.return_value["reviewed"] = True
    page = await mcp_video_tools.dispatch("visual_get_video_scan", {"scan_id": 7}, None)
    assert "private" not in str(page)
    assert page["frames"] == [{"index": 8, "time": 24, "image_url": "https://media.example/frame.jpg"}]
    assert not allowed("public", "visual_scan_cutscene") and allowed(
        "internal", "visual_scan_cutscene")
    assert "visual_scan_cutscene" in {item["name"]
                                      for item in internal_definitions()}
    assert "visual_scan_cutscene" not in {
        item["name"] for item in read_definitions()}
    assert "visual_scan_cutscene" not in {item.name for item in await handle_list_tools()}
    assert "error" in await mcp_video_tools.dispatch("visual_scan_cutscene", {"asset_node_id": 3}, None)
    with pytest.raises(ValueError):
        await mcp_video_tools.enqueue_internal({"asset_node_id": 3, "asset_version": "3.7", "frame_batch_size": 49}, None)
    assert not service.enqueue.await_count


@pytest.mark.asyncio
async def test_mcp_images_and_internal_video_role(setup, monkeypatch):
    import base64
    from contextlib import asynccontextmanager

    from wuwa_story.agents.capabilities import allowed, execute_internal, internal_definitions
    from wuwa_story.api.routes.mcp import handle_call_tool
    from wuwa_story.db import session as session_module

    _, _, service = setup
    service.get_scan.return_value = {"id": 7, "status": "succeeded", "reviewed": True,
        "frames": [{"index": 13, "time": 39, "image_url": "/frame", "detections": [{"actor": "private"}]}]}
    service.frame_bytes.return_value = b"\xff\xd8\xffjpeg"

    @asynccontextmanager
    async def session_factory():
        yield None

    monkeypatch.setattr(session_module, "SessionFactory", session_factory)
    result = await handle_call_tool("visual_get_video_scan", {"scan_id": 7, "frame_offset": 13, "frame_limit": 1})
    assert len(result) == 2 and result[1].type == "image" and result[1].mimeType == "image/jpeg"
    assert base64.b64decode(result[1].data) == b"\xff\xd8\xffjpeg"
    assert "private" not in result[0].text
    service.frame_bytes.assert_awaited_once_with(7, 13)
    assert not allowed("public", "visual_read_video_scan") and allowed("internal", "visual_read_video_scan")
    assert "visual_read_video_scan" in {item["name"] for item in internal_definitions()}
    internal = await execute_internal(None, None, "visual_read_video_scan", {"scan_id": 7, "frame_offset": 13})
    assert internal["frames"][0]["detections"][0]["actor"] == "private"
    service.get_scan.return_value["reviewed"] = False
    service.frame_bytes.reset_mock()
    result = await handle_call_tool("visual_get_video_scan", {"scan_id": 7})
    assert len(result) == 1 and "unavailable" in result[0].text
    service.frame_bytes.assert_not_awaited()
    reviewed = {**service.get_scan.return_value, "reviewed": True}
    service.get_scan.side_effect = [reviewed, {**reviewed, "reviewed": False}]
    result = await handle_call_tool("visual_get_video_scan", {"scan_id": 7})
    assert len(result) == 1
    service.frame_bytes.assert_not_awaited()
    service.get_scan.side_effect = None
    service.get_scan.return_value = reviewed
    service.frame_bytes.return_value = b"<html>not an image</html>"
    result = await handle_call_tool("visual_get_video_scan", {"scan_id": 7})
    assert len(result) == 1 and all(item.type != "image" for item in result)
    with pytest.raises(ValueError):
        await mcp_video_tools.frame_contents("visual_get_video_scan", {"scan_id": 7, "frame_limit": 5}, None)


@pytest.mark.asyncio
async def test_real_service_paging_and_review_on_disposable_postgres(tmp_path, monkeypatch):
    from PIL import Image
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from wuwa_story.db.models import (
        FileLocation,
        FileObject,
        FileType,
        ProcessingRun,
        Processor,
        VisualVideoFrame,
    )
    from wuwa_story.storage.local import LocalStorage
    from wuwa_story.visual_memory.service import VisualMemoryService
    from wuwa_story.visual_memory.video import PROCESSOR, VideoReviewService

    url = os.getenv("WUWA_VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WUWA_VISUAL_TEST_DATABASE_URL to a disposable migrated database")
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(connection, expire_on_commit=False, autoflush=False,
                                join_transaction_mode="create_savepoint") as session:
            await session.execute(insert(Processor).values(key=PROCESSOR, version="test")
                                  .on_conflict_do_nothing(index_elements=[Processor.key]))
            processor = await session.scalar(select(Processor.id).where(Processor.key == PROCESSOR))
            run = ProcessingRun(processor_id=processor, status="succeeded", metadata_json={
                "source": {"source_sha256": "ab" * 32}, "reviewed": False,
                "interval_seconds": 3, "frame_batch_size": 12,
            }, raw_output={"completed_frames": 60, "total_frames": 60,
                           "total_detections": 2, "unresolved_count": 2})
            session.add(run)
            await session.flush()
            file_type = await session.scalar(select(FileType).where(FileType.key == "image"))
            frame_file = FileObject(file_type_id=file_type.id, mime_type="image/jpeg", size_bytes=3,
                                    sha256=bytes.fromhex("ab" * 32))
            session.add(frame_file)
            await session.flush()
            storage = LocalStorage(tmp_path)
            Image.new("RGB", (10, 10), "red").save(tmp_path / "sample", format="JPEG")
            session.add(FileLocation(file_id=frame_file.id, backend=storage.backend,
                                     bucket=storage.bucket, object_key="sample", available=True, is_primary=True))
            session.add_all(VisualVideoFrame(scan_id=run.id, frame_index=i, time=i * 3,
                file_id=frame_file.id, width=10, height=10, detections=(
                    [{"index": 0, "bbox": [0, 0, 10, 10], "decision": "unresolved", "links": []}]
                    if i in (0, 52) else [])) for i in range(60))
            await session.flush()
            api = FastAPI()
            api.include_router(video.router)
            search = AsyncMock(return_value={"candidates": [{"identity_id": 7, "evidence": [1, 2, 3]}]})
            monkeypatch.setattr(VisualMemoryService, "search", search)
            monkeypatch.setattr(video, "service_for", lambda db: VideoReviewService(db, storage=storage))
            api.dependency_overrides[get_session] = lambda: session
            api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=42)
            api.dependency_overrides[require_csrf] = lambda: None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
                base = f"/admin/visual-memory/videos/scans/{run.id}"
                page = (await client.get(base, params={"frame_offset": 48, "frame_limit": 12})).json()
                assert [frame["index"] for frame in page["frames"]] == list(range(48, 60))
                assert page["frame_count"] == 60 and page["unresolved_count"] == 2
                add = base + "/frames/52/observations?frame_offset=48&frame_limit=12"
                assert (await client.post(add, json={"bbox": [-1, 0, 2, 2]})).status_code == 422
                assert (await client.post(add, json={"bbox": [0, 0, 11, 2]})).status_code == 422
                assert (await client.post(add, json={"bbox": [True, 0, 2, 2]})).status_code == 422
                assert (await client.post(add, json={"bbox": [1, 1, 2]})).status_code == 422
                response = await client.post(add, json={"bbox": [1.2, 2.1, 6.1, 8.1]})
                assert response.status_code == 200, response.text
                page = response.json()
                assert page["frame_offset"] == 48 and page["unresolved_count"] == 3
                assert page["total_detections"] == 3
                observation = page["frames"][4]["detections"][1]
                assert observation["index"] == 1 and observation["bbox"] == [1, 2, 7, 9]
                assert observation["normalized_bbox"] == [0.1, 0.2, 0.7, 0.9]
                assert observation["detection"]["status"] == "manual"
                assert observation["candidates"][0]["evidence"] == [1, 2]
                retry = await client.post(add, json={"bbox": [1, 2, 7, 9]})
                assert retry.status_code == 200 and retry.json()["unresolved_count"] == 3
                assert search.await_count == 1
                patch_url = base + "/observations/52/1/bbox?frame_offset=48&frame_limit=12"
                frame = await session.scalar(select(VisualVideoFrame).where(
                    VisualVideoFrame.scan_id == run.id, VisualVideoFrame.frame_index == 52))
                detections = list(frame.detections)
                detections[1] = {**detections[1], "links": [{"identity_id": 7}]}
                frame.detections = detections
                await session.flush()
                edited = await client.patch(patch_url, json={"bbox": [2, 3, 8, 9]})
                assert edited.status_code == 200 and edited.json()["unresolved_count"] == 3
                assert edited.json()["frames"][4]["detections"][1]["bbox"] == [2, 3, 8, 9]
                assert edited.json()["frames"][4]["detections"][1]["links"] == []
                assert edited.json()["total_detections"] == 3
                assert search.await_count == 2
                assert (await client.patch(patch_url, json={"bbox": [2, 3, 10.1, 9]})).status_code == 422
                frame = await session.scalar(select(VisualVideoFrame).where(
                    VisualVideoFrame.scan_id == run.id, VisualVideoFrame.frame_index == 52))
                detections = list(frame.detections)
                detections[1] = {**detections[1], "reference_id": 999, "links": [{"identity_id": 7}]}
                frame.detections = detections
                await session.flush()
                assert (await client.patch(patch_url, json={"bbox": [1, 1, 4, 4]})).status_code == 422
                detections[1] = {**detections[1], "reference_id": None}
                frame.detections = detections
                await session.flush()
                assert (await client.post(base + "/reviewed")).status_code == 422
                assert (await client.post(base + "/observations/52/0/resolve", json={"decision": "reject"})).status_code == 200
                assert (await client.post(base + "/reject-all")).status_code == 200
                response = await client.post(base + "/reviewed")
                assert response.status_code == 200 and response.json()["reviewed"] is True
                assert (await client.post(add, json={"bbox": [1, 1, 4, 4]})).status_code == 422
                assert (await client.patch(patch_url, json={"bbox": [1, 1, 4, 4]})).status_code == 422
        await transaction.rollback()
    await engine.dispose()
