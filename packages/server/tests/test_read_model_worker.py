import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.read_models import worker
from wuwa_story.read_models.context import SOURCE_BUILD, source_build


def test_source_context_is_nested_and_restored_after_failure():
    assert not SOURCE_BUILD.get()
    with source_build():
        with pytest.raises(RuntimeError), source_build():
            assert SOURCE_BUILD.get()
            raise RuntimeError("source unavailable")
        assert SOURCE_BUILD.get()
    assert not SOURCE_BUILD.get()


@pytest.mark.asyncio
async def test_failed_entity_does_not_stop_other_durable_refreshes(monkeypatch):
    claims = [SimpleNamespace(kind="character", entity_id=i) for i in (1, 2)]
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=claims))
    contexts = []

    async def refresh(factory, claim):
        contexts.append(SOURCE_BUILD.get())
        if claim.entity_id == 1:
            raise RuntimeError("temporary source error")

    monkeypatch.setattr(worker, "refresh_claim", refresh)
    assert await worker.refresh_batch(object(), limit=2) == 2
    assert contexts == [True, True]
    assert not SOURCE_BUILD.get()


@pytest.mark.asyncio
async def test_shutdown_cancels_build_instead_of_hiding_cancellation(monkeypatch):
    monkeypatch.setattr(worker, "claim_refreshes", AsyncMock(return_value=[object()]))
    monkeypatch.setattr(worker, "refresh_claim", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await worker.refresh_batch(object())
    assert not SOURCE_BUILD.get()


@pytest.mark.asyncio
async def test_watch_keeps_pending_work_when_disk_headroom_is_low(monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(read_projection_min_free_gib=20)
    )
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024))
    refresh = AsyncMock()
    monkeypatch.setattr(worker, "refresh_batch", refresh)

    async def finish_wait(awaitable, *, timeout):
        stop.set()
        await awaitable

    monkeypatch.setattr(worker.asyncio, "wait_for", finish_wait)
    await worker.watch_refreshes(stop)
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_watch_passes_selected_kinds_to_refresh(monkeypatch):
    stop = asyncio.Event()
    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(read_projection_min_free_gib=20)
    )
    monkeypatch.setattr(worker.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))

    async def refresh(factory, *, kinds):
        assert kinds == ["character"]
        stop.set()
        return 1

    monkeypatch.setattr(worker, "refresh_batch", refresh)
    await worker.watch_refreshes(stop, kinds=["character"])


@pytest.mark.asyncio
async def test_estimate_is_read_only_and_does_not_run_refresh(monkeypatch, capsys):
    from wuwa_story.read_models.refresh import BackfillEstimate

    estimate = BackfillEstimate("character", "current", 1, 2, 2, 4096, 1, 1024)
    monkeypatch.setattr(worker, "estimate_backfill", AsyncMock(return_value=estimate))
    refresh = AsyncMock()
    monkeypatch.setattr(worker, "refresh_batch", refresh)
    monkeypatch.setattr(worker, "engine", SimpleNamespace(dispose=AsyncMock()))
    await worker.main(SimpleNamespace(kind=["character"], snapshot="current", estimate=True))
    assert '"estimated_bytes": 4096' in capsys.readouterr().out
    refresh.assert_not_awaited()
    worker.engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_pilot_requires_one_kind_and_valid_entity_id(monkeypatch):
    monkeypatch.setattr(worker, "engine", SimpleNamespace(dispose=AsyncMock()))
    with pytest.raises(ValueError, match="exactly one"):
        await worker.main(
            SimpleNamespace(
                kind=None,
                snapshot="current",
                estimate=False,
                entity_id=[7],
                backfill=True,
            )
        )
    with pytest.raises(ValueError, match="positive"):
        await worker.main(
            SimpleNamespace(
                kind=["character"],
                snapshot="current",
                estimate=False,
                entity_id=[0],
                backfill=True,
            )
        )
