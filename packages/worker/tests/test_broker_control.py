from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import broker
from wuwa_story_worker.queues import queue_concurrency


@pytest.mark.asyncio
@pytest.mark.parametrize("status,redelivered,expected", [
    ("cancelled", True, False), ("completed", False, False),
    ("running", False, False), ("running", True, True),
    ("queued", False, True), ("waiting_dependency", False, True),
])
async def test_import_claim_respects_cancellation_and_duplicate_delivery(monkeypatch, status, redelivered, expected):
    run = SimpleNamespace(status=status)
    session = AsyncMock()
    session.scalar.return_value = run
    session.__aenter__.return_value = session
    monkeypatch.setattr(broker, "SessionFactory", lambda: session)
    assert await broker.claim_import({"run_id": 1}, redelivered=redelivered) is expected
    if expected:
        assert run.status == "running"
        session.commit.assert_awaited_once()
    else:
        session.commit.assert_not_awaited()


def test_media_parallelism_is_bounded_and_heavy_extractors_remain_serial(monkeypatch):
    monkeypatch.delenv("WUWA_QUEUE_CONCURRENCY", raising=False)
    limits = queue_concurrency()
    assert limits["release_media"] == limits["entity_media"] == 2
    assert limits["asset_extract"] == limits["asset_download"] == limits["event_media"] == 1
