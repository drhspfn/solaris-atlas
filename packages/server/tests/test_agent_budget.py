import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents.budget import BudgetExceeded, reserve, settle
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
            assert await settle(session, call.id, settings, 20, 0, {"usage": "reported"})
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
