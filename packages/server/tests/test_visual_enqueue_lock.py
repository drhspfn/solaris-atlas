import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents.cutscene_vision import enqueue_visual_job
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.agents.visual_enqueue_lock import lock_visual_sources


@pytest.mark.asyncio
async def test_legacy_enqueue_rejects_existing_logical_scene_before_storage():
    db = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(return_value=55), get=AsyncMock())
    ref = SimpleNamespace(owner_node_id=10016, metadata_json={"asset_version": "3.8.0"})
    with pytest.raises(ValueError, match="logical scene analysis already exists"):
        await enqueue_visual_job(db, ref, AgentSettings(_env_file=None, vision_enabled=True))
    db.execute.assert_awaited_once()
    db.get.assert_not_awaited()
    params = db.scalar.call_args.args[0].compile().params
    assert [10016] in params.values() and "3.8.0" in params.values()


@pytest.mark.asyncio
async def test_locks_are_deduplicated_and_ordered():
    db = SimpleNamespace(execute=AsyncMock())
    await lock_visual_sources(db, [10016, 10015, 10016], "3.8.0")
    actual = [call.args[1]["key"] for call in db.execute.await_args_list]
    first, second = SimpleNamespace(execute=AsyncMock()), SimpleNamespace(execute=AsyncMock())
    await lock_visual_sources(first, [10015], "3.8.0")
    await lock_visual_sources(second, [10016], "3.8.0")
    assert actual == [first.execute.await_args.args[1]["key"], second.execute.await_args.args[1]["key"]]


@pytest.mark.asyncio
async def test_partial_legacy_scene_blocks_enqueue():
    from wuwa_story.api.routes.cutscene_analysis import _guard_partial_legacy_jobs

    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(sha256=b"old")),
                         scalar=AsyncMock(return_value=42))
    with pytest.raises(ValueError, match="scene part already exists"):
        await _guard_partial_legacy_jobs(db, "3.8.0", {1: SimpleNamespace(file_id=7)})


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WUWA_TEST_DATABASE_URL"), reason="Isolated PostgreSQL required")
async def test_postgres_two_transactions_serialize_shared_source():
    engine = create_async_engine(os.environ["WUWA_TEST_DATABASE_URL"])
    try:
        async with AsyncSession(engine) as first, AsyncSession(engine) as second:
            await lock_visual_sources(first, [10015, 10016], "3.8.0")
            started = asyncio.Event()

            async def legacy():
                started.set()
                await lock_visual_sources(second, [10016], "3.8.0")
                return await second.scalar(text("SELECT 1"))

            task = asyncio.create_task(legacy())
            await started.wait()
            await asyncio.sleep(0.1)
            assert not task.done()
            await first.commit()
            assert await asyncio.wait_for(task, 3) == 1
            await second.rollback()
    finally:
        await engine.dispose()
