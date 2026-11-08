import asyncio
import hashlib
import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from test_visual_memory_readonly import ControlledEngine
from wuwa_story.db.models import Node, NodeType, ProcessingRun, User
from wuwa_story.storage.local import LocalStorage
from wuwa_story.storage.service import FileRegistrationService
from wuwa_story.visual_memory import video
from wuwa_story.visual_memory.video import VideoReviewService


class VideoEngine(ControlledEngine):
    def model_identity(self):
        return {"embedding": self.key, "detector": "controlled"}

    def detect(self, image):
        return [{"bbox": [0, 0, image.width, image.height], "score": 0.9}]


@pytest_asyncio.fixture
async def setup_scan(tmp_path, monkeypatch):
    url = os.getenv("WUWA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set WUWA_TEST_DATABASE_URL to a PostgreSQL server with CREATEDB")
    name = "progressive_test_" + uuid4().hex
    owner = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    test_url = make_url(url).set(database=name).render_as_string(hide_password=False)
    async with owner.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    db = None
    try:
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": test_url, "MIGRATION_DATABASE_URL": test_url,
                 "WUWA_ONLINE_SEARCH_INDEX_ONLY": "0", "WUWA_DB_TRANSACTION_POOLING": "0"},
            capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        db = create_async_engine(test_url, poolclass=NullPool)
        storage = LocalStorage(tmp_path)
        engine = VideoEngine("progressive-" + uuid4().hex)
        path = tmp_path / "source.mp4"
        path.write_bytes(b"controlled video")
        async with AsyncSession(db, expire_on_commit=False, autoflush=False) as session:
            user = User(email=name + "@example.invalid", email_normalized=name + "@example.invalid",
                        nickname=name[-24:], nickname_normalized=name[-24:], password_hash="unused", role=2)
            session.add(user)
            node_type = await session.scalar(select(NodeType.id).where(NodeType.key == "media_asset"))
            node = Node(type_id=node_type, canonical_key=name)
            session.add(node)
            file = await FileRegistrationService(storage).register_file(
                session, path, "video_mp4", mime_type="video/mp4")
            await session.flush()
            source = {"file_id": file.id, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "size_bytes": path.stat().st_size}
            actor, node_id = user.id, node.id
            await session.commit()
        async def published(*args):
            return dict(source)
        monkeypatch.setattr(VideoReviewService, "_published", published)
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(image, format="JPEG")
        monkeypatch.setattr(video, "probe_duration", lambda _: 1)
        monkeypatch.setattr(video, "sample_times", lambda *args: [0, 0.5])
        monkeypatch.setattr(video, "extract_frame", lambda *args: image.getvalue())
        yield SimpleNamespace(db=db, storage=storage, engine=engine, actor=actor, node_id=node_id)
    finally:
        if db:
            await db.dispose()
        async with owner.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await owner.dispose()


def service(fixture, session):
    return VideoReviewService(session, fixture.storage, fixture.engine)


@pytest.mark.asyncio
async def test_progressive_review_and_worker_counter_interleaving(setup_scan):
    f = setup_scan
    async with AsyncSession(f.db, expire_on_commit=False, autoflush=False) as worker_session, \
            AsyncSession(f.db, expire_on_commit=False, autoflush=False) as review_session:
        worker, reviewer = service(f, worker_session), service(f, review_session)
        scan = await worker.enqueue(f.node_id)
        await worker_session.commit()
        first_saved = asyncio.Event()
        continue_worker = asyncio.Event()
        worker_waiting_for_lock = asyncio.Event()
        identify = worker.memory.identify
        original_run = worker._run
        calls = 0
        async def identify_with_pause(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                first_saved.set()
                await continue_worker.wait()
            return await identify(*args, **kwargs)
        async def observe_lock(scan_id, lock=False):
            if lock and calls == 2:
                worker_waiting_for_lock.set()
            return await original_run(scan_id, lock=lock)
        worker.memory.identify = identify_with_pause
        worker._run = observe_lock
        task = asyncio.create_task(worker.process(scan["id"]))
        try:
            await asyncio.wait_for(first_saved.wait(), 15)
            partial = await reviewer.get_scan(scan["id"])
            assert partial["status"] == "running" and partial["can_review_frames"]
            assert partial["frame_count"] == 1 and partial["unresolved_count"] == 1
            with pytest.raises(ValueError, match="Frame does not exist"):
                await reviewer.resolve(scan["id"], 10, 0, "reject", actor_id=f.actor)
            await review_session.rollback()
            with pytest.raises(ValueError, match="Sampling must complete"):
                await reviewer.mark_reviewed(scan["id"], actor_id=f.actor)
            await review_session.rollback()
            with pytest.raises(ValueError, match="completed"):
                await reviewer.reject_all(scan["id"], actor_id=f.actor)
            await review_session.rollback()
            edited = await reviewer.edit_bbox(scan["id"], 0, [1, 1, 10, 10])
            assert edited["unresolved_count"] == 2
            resolved = await reviewer.resolve(scan["id"], 0, 0, "reject", actor_id=f.actor)
            assert resolved["unresolved_count"] == 1
            continue_worker.set()
            await asyncio.wait_for(worker_waiting_for_lock.wait(), 15)
            assert not task.done()
            await review_session.commit()
            done = await asyncio.wait_for(task, 15)
            assert done["status"] == "succeeded" and done["frame_count"] == 2
            assert done["total_detections"] == 3 and done["unresolved_count"] == 2
            assert done["frames"][0]["detections"][0]["decision"] == "reject"
            assert done["frames"][0]["detections"][1]["bbox"] == [1, 1, 10, 10]
        finally:
            continue_worker.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_dedup_resume_and_partial_statuses(setup_scan):
    f = setup_scan
    async with AsyncSession(f.db, expire_on_commit=False, autoflush=False) as session:
        review = service(f, session)
        scan = await review.enqueue(f.node_id)
        pending = await review.enqueue(f.node_id)
        assert pending["id"] == scan["id"] and pending["duplicate"]
        await session.commit()
        identify = review.memory.identify
        calls = 0
        async def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("interrupt")
            return await identify(*args, **kwargs)
        review.memory.identify = interrupted
        with pytest.raises(RuntimeError, match="interrupt"):
            await review.process(scan["id"])
        await session.rollback()
        active = await review.enqueue(f.node_id)
        assert active["duplicate"] and active["status"] == "running"
        await review.resolve(scan["id"], 0, 0, "reject", actor_id=f.actor)
        run = await review._run(scan["id"], lock=True)
        run.status = "failed"
        await session.commit()
        failed = await review.get_scan(scan["id"])
        assert failed["can_review_frames"]
        await review.edit_bbox(scan["id"], 0, [1, 1, 10, 10])
        await session.commit()
        run = await review._run(scan["id"], lock=True)
        run.status = "pending"
        await session.commit()
        pending = await review.edit_bbox(scan["id"], 0, [2, 2, 11, 11], detection_index=1)
        assert pending["can_review_frames"] and pending["frames"][0]["detections"][1]["bbox"] == [2, 2, 11, 11]
        await session.commit()
        review.memory.identify = identify
        done = await review.process(scan["id"])
        assert done["frame_count"] == 2 and done["total_detections"] == 3
        assert done["unresolved_count"] == 2
        assert done["frames"][0]["detections"][0]["decision"] == "reject"
        duplicate = await review.enqueue(f.node_id)
        assert duplicate["id"] == scan["id"] and duplicate["duplicate"]
        assert duplicate["status"] == "succeeded"
        run = await session.get(ProcessingRun, scan["id"])
        run.metadata_json = {**run.metadata_json, "reviewed": True}
        await session.flush()
        assert not (await review.get_scan(scan["id"]))["can_review_frames"]
        with pytest.raises(ValueError, match="unreviewed"):
            await review.resolve(scan["id"], 0, 1, "reject", actor_id=f.actor)



@pytest.mark.asyncio
async def test_edit_search_releases_run_lock_and_rechecks_observations(setup_scan):
    f = setup_scan
    async with AsyncSession(f.db, expire_on_commit=False, autoflush=False) as editing_session, \
            AsyncSession(f.db, expire_on_commit=False, autoflush=False) as review_session:
        editor, reviewer = service(f, editing_session), service(f, review_session)
        scan = await editor.enqueue(f.node_id)
        await editing_session.commit()
        identify = editor.memory.identify
        calls = 0
        async def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("interrupt")
            return await identify(*args, **kwargs)
        editor.memory.identify = interrupted
        with pytest.raises(RuntimeError, match="interrupt"):
            await editor.process(scan["id"])
        await editing_session.rollback()
        searching, resume = asyncio.Event(), asyncio.Event()
        search = editor.memory.search
        async def paused_search(*args, **kwargs):
            searching.set()
            await resume.wait()
            return await search(*args, **kwargs)
        editor.memory.search = paused_search
        task = asyncio.create_task(editor.edit_bbox(scan["id"], 0, [1, 1, 10, 10], detection_index=0))
        try:
            await asyncio.wait_for(searching.wait(), 15)
            await asyncio.wait_for(reviewer.resolve(scan["id"], 0, 0, "reject", actor_id=f.actor), 15)
            await review_session.commit()
            resume.set()
            with pytest.raises(ValueError, match="observations changed"):
                await asyncio.wait_for(task, 15)
        finally:
            resume.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
