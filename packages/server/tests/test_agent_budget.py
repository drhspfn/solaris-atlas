import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents.budget import BudgetExceeded, reported_price, reserve, settle
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall, AgentDailyUsage

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Isolated test database required")


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_overspend_and_settlement_is_idempotent():
    engine = create_async_engine(DATABASE_URL)
    settings = AgentSettings(
        _env_file=None,
        api_key="test",
        daily_budget_usd="0.00010000",
        input_usd_per_million=1,
        output_usd_per_million=0,
        price_safety_multiplier=1,
    )
    ids = []

    async def claim():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                call = await reserve(
                    session, settings, run_id=None, step=0, input_bound=60, output_bound=0
                )
                await session.commit()
                ids.append(call.id)
                return call.id
            except BudgetExceeded:
                await session.rollback()
                return None

    try:
        results = await asyncio.gather(claim(), claim())
        assert len([value for value in results if value is not None]) == 1
        async with AsyncSession(engine, expire_on_commit=False) as session:
            call = await session.get(AgentCall, ids[0])
            assert await settle(
                session,
                call.id,
                settings,
                20,
                0,
                {"usage": {"input_tokens": 20, "output_tokens": 0}},
            )
            await session.commit()
            usage = await session.get(AgentDailyUsage, call.day)
            assert usage.reserved_usd == 0 and usage.spent_usd == Decimal("0.00002000")
            assert await settle(session, call.id, settings, 20, 0, {})
            await session.commit()
            await session.refresh(usage)
            assert usage.spent_usd == Decimal("0.00002000")
    finally:
        async with engine.begin() as db:
            from sqlalchemy import delete

            await db.execute(delete(AgentCall).where(AgentCall.id.in_(ids)))
            await db.execute(
                delete(AgentDailyUsage).where(
                    ~select(AgentCall.id).where(AgentCall.day == AgentDailyUsage.day).exists()
                )
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_calls_keep_reservation_and_token_limit_is_enforced():
    engine = create_async_engine(DATABASE_URL)
    settings = AgentSettings(
        _env_file=None, api_key="test", daily_budget_usd=1, daily_token_limit=100
    )
    async with AsyncSession(engine) as session:
        try:
            call = await reserve(
                session, settings, run_id=None, step=0, input_bound=70, output_bound=10
            )
            call.status = "uncertain"
            await session.flush()
            with pytest.raises(BudgetExceeded):
                await reserve(
                    session, settings, run_id=None, step=1, input_bound=50, output_bound=0
                )
            usage = await session.get(AgentDailyUsage, call.day)
            assert usage.reserved_tokens == 80 and usage.spent_tokens == 0
        finally:
            await session.rollback()
    await engine.dispose()


@pytest.mark.parametrize(
    "details",
    [
        {"cached_tokens": -1},
        {"cached_tokens": True},
        {"cache_write_tokens": "2"},
        {"cached_tokens": 90, "cache_write_tokens": 11},
    ],
)
def test_invalid_cache_usage_is_rejected(details):
    with pytest.raises(ValueError, match="Invalid cache usage"):
        reported_price(
            AgentSettings(_env_file=None),
            100,
            10,
            {"usage": {"input_tokens_details": details}},
            "analysis",
        )


def test_cache_reads_writes_and_legacy_prices():
    settings = AgentSettings(
        _env_file=None,
        cached_input_usd_per_million="0.01",
        cache_write_usd_per_million="0.125",
        price_safety_multiplier=1,
    )
    raw = {"usage": {"input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}}}
    assert reported_price(settings, 1000, 10, raw, "analysis") == Decimal("0.00003550")
    assert reported_price(settings, 1000, 10, {}, "analysis") == Decimal("0.00010500")
    settings.cached_input_usd_per_million = settings.cache_write_usd_per_million = None
    assert reported_price(settings, 1000, 10, raw, "analysis") == Decimal("0.00010500")


@pytest.mark.asyncio
async def test_optional_token_guard_and_cached_settlement_keep_usd_cap():
    engine = create_async_engine(DATABASE_URL)
    settings = AgentSettings(
        _env_file=None,
        api_key="test",
        daily_budget_usd="0.0002",
        daily_token_limit=0,
        cached_input_usd_per_million="0.01",
        cache_write_usd_per_million="0.125",
        price_safety_multiplier=1,
    )
    async with AsyncSession(engine, expire_on_commit=False) as db:
        try:
            call = await reserve(
                db, settings, run_id=None, step=0, input_bound=1000, output_bound=10
            )
            assert call.reserved_usd == Decimal("0.00013000")
            usage = await db.get(AgentDailyUsage, call.day)
            usage.spent_tokens = 2_000_000
            raw = {
                "usage": {"input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}}
            }
            assert await settle(db, call.id, settings, 1000, 10, raw)
            await db.flush()
            assert usage.spent_usd == Decimal("0.00003550")
            assert usage.spent_tokens == 2_001_010 and usage.reserved_tokens == 0
            assert await settle(db, call.id, settings, 1000, 10, raw)
            assert usage.spent_usd == Decimal("0.00003550")
            with pytest.raises(BudgetExceeded, match="USD"):
                await reserve(db, settings, run_id=None, step=1, input_bound=2000, output_bound=10)
        finally:
            await db.rollback()
    await engine.dispose()
