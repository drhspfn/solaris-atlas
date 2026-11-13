from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import DBAPIError

from wuwa_story.ingestion import compiler_importer


@pytest.mark.asyncio
async def test_commit_localization_batch_retries_on_deadlock(monkeypatch):
    calls = 0

    async def fake_import(session, release_id, batch):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DBAPIError(
                "INSERT INTO i18n.localization_key ...",
                {},
                SimpleNamespace(sqlstate="40P01"),
            )
        return 2, 4

    monkeypatch.setattr(compiler_importer, "import_localization_batch", fake_import)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    keys, values = await compiler_importer.CompiledDatasetImporter._commit_localization_batch(
        session, 22, [{"key": "k1"}]
    )

    assert (keys, values) == (2, 4)
    assert calls == 2
    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_localization_batch_raises_non_deadlock(monkeypatch):
    async def fake_import(session, release_id, batch):
        raise DBAPIError(
            "INSERT INTO i18n.localization_key ...",
            {},
            SimpleNamespace(sqlstate="23505"),
        )

    monkeypatch.setattr(compiler_importer, "import_localization_batch", fake_import)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    with pytest.raises(DBAPIError):
        await compiler_importer.CompiledDatasetImporter._commit_localization_batch(
            session, 22, [{"key": "k1"}]
        )

    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()
