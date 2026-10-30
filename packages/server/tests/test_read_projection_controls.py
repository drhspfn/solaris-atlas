from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from wuwa_story.api.routes import read_projections as routes
from wuwa_story.auth.dependencies import require_admin, require_csrf


def test_projection_controls_require_admin_and_mutation_requires_csrf():
    assert any(dep.dependency is require_admin for dep in routes.router.dependencies)
    mutation = next(route for route in routes.router.routes if "POST" in route.methods)
    assert any(dep.dependency is require_csrf for dep in mutation.dependencies)


def test_rebuild_rejects_unknown_kind_and_snapshot_and_deduplicates():
    with pytest.raises(ValidationError):
        routes.RebuildProjection(kinds=["arbitrary_table"])
    with pytest.raises(ValidationError):
        routes.RebuildProjection(kinds=["character"], snapshot_key="../../raw")
    assert routes.RebuildProjection(kinds=["character", "character"]).kinds == ["character"]


@pytest.mark.asyncio
async def test_rebuild_historical_requires_import_and_keeps_queue_durable(monkeypatch):
    database = SimpleNamespace(scalar=AsyncMock(return_value=7), commit=AsyncMock())
    dirty = AsyncMock()
    monkeypatch.setattr(routes, "mark_dirty", dirty)
    result = await routes.rebuild_projection(
        routes.RebuildProjection(kinds=["character"], snapshot_key="3.3.0"), database
    )
    dirty.assert_awaited_once_with(database, "character", [0], "3.3.0")
    database.commit.assert_awaited_once()
    assert result["queued_scopes"] == 1


@pytest.mark.asyncio
async def test_unimported_snapshot_is_not_queued(monkeypatch):
    database = SimpleNamespace(scalar=AsyncMock(return_value=None), commit=AsyncMock())
    dirty = AsyncMock()
    monkeypatch.setattr(routes, "mark_dirty", dirty)
    with pytest.raises(HTTPException) as failure:
        await routes.rebuild_projection(
            routes.RebuildProjection(kinds=["character"], snapshot_key="3.3.0"), database
        )
    assert failure.value.status_code == 404
    statement = str(database.scalar.await_args.args[0])
    assert "ops.import_run" in statement
    dirty.assert_not_awaited()
    database.commit.assert_not_awaited()
