from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from wuwa_story.api.routes.admin_data import (
    SnapshotImportRequest,
    enqueue_snapshot_import,
    router,
)
from wuwa_story.auth.dependencies import require_admin, require_csrf


def test_data_operations_mutation_requires_admin_and_csrf():
    post = next(route for route in router.routes if "POST" in route.methods)
    dependencies = [dependency.call for dependency in post.dependant.dependencies]
    assert require_admin in dependencies
    assert require_csrf in dependencies


def test_snapshot_import_accepts_only_major_minor_versions():
    assert SnapshotImportRequest(version="3.7").version == "3.7"
    with pytest.raises(ValidationError):
        SnapshotImportRequest(version="3.7.0")


@pytest.mark.asyncio
async def test_snapshot_import_rejects_when_upstream_has_no_matching_branch(monkeypatch):
    monkeypatch.setattr(
        "wuwa_story.api.routes.admin_data.discover_remote_snapshots",
        lambda *_: (_ for _ in ()).throw(ValueError("no branch")),
    )
    with pytest.raises(HTTPException) as error:
        await enqueue_snapshot_import(SnapshotImportRequest(version="3.7"), AsyncMock())
    assert error.value.status_code == 502
