from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion.import_progress import ImportProgress


def run():
    return SimpleNamespace(id=12, release_id=4, importer="test", importer_version="2",
                           metadata_json={}, records_seen=0, records_created=0)


@pytest.mark.asyncio
async def test_resume_reuses_only_committed_phases_and_does_not_count_old_inserts():
    previous = SimpleNamespace(metadata_json={"stages": {
        "raw": {"counts": [1000, 1000], "seconds": 9}}})
    session = SimpleNamespace(scalar=AsyncMock(return_value=previous), commit=AsyncMock())
    current = run()
    progress = await ImportProgress.start(session, current, b'{"commit":"pinned"}')
    raw = AsyncMock(side_effect=AssertionError("must not replay committed raw stage"))
    entities = AsyncMock(return_value=(50, 20))
    assert await progress.phase("raw", raw) == (1000, 0)
    assert await progress.phase("entities", entities) == (50, 20)
    assert current.records_seen == 1050
    assert current.records_created == 20
    assert "source_fingerprint" in current.metadata_json
    entities.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_does_not_publish_a_phase_checkpoint():
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), commit=AsyncMock())
    progress = await ImportProgress.start(session, run(), b"manifest")
    with pytest.raises(RuntimeError, match="interrupted"):
        await progress.phase("raw", AsyncMock(side_effect=RuntimeError("interrupted")))
    assert progress.stages == {}
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_query_pins_dataset_identity_and_importer_version():
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), commit=AsyncMock())
    await ImportProgress.start(session, run(), b"manifest")
    statement = session.scalar.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "source_fingerprint" in sql
    assert "importer_version = '2'" in sql
    assert "release_id = 4" in sql
