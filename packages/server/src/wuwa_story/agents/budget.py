"""Atomic shared reservations before remote requests; uncertain calls stay charged."""

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall, AgentDailyUsage


class BudgetExceeded(ValueError):
    pass


def price(
    input_tokens: int,
    output_tokens: int,
    input_rate: Decimal,
    output_rate: Decimal,
    multiplier: Decimal,
) -> Decimal:
    if min(input_tokens, output_tokens) < 0 or min(input_rate, output_rate) < 0:
        raise ValueError("Token counts and prices cannot be negative")
    return (
        (input_tokens * input_rate + output_tokens * output_rate) * multiplier / Decimal(1_000_000)
    ).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)


async def reserve(
    session: AsyncSession,
    settings: AgentSettings,
    *,
    run_id: int | None,
    step: int,
    input_bound: int,
    output_bound: int,
    kind: str = "analysis",
) -> AgentCall:
    settings.require_enabled()
    if input_bound > settings.max_input_tokens:
        raise BudgetExceeded("Input context exceeds the configured token bound")
    day = datetime.now(UTC).astimezone(ZoneInfo(settings.budget_timezone)).date()
    await session.execute(insert(AgentDailyUsage).values(day=day).on_conflict_do_nothing())
    usage = await session.scalar(
        select(AgentDailyUsage).where(AgentDailyUsage.day == day).with_for_update()
    )
    assert usage is not None
    rate = (
        settings.embedding_input_usd_per_million
        if kind == "embedding"
        else settings.input_usd_per_million
    )
    amount = price(
        input_bound,
        output_bound,
        rate,
        settings.output_usd_per_million,
        settings.price_safety_multiplier,
    )
    tokens = input_bound + output_bound
    if usage.spent_usd + usage.reserved_usd + amount > settings.daily_budget_usd:
        raise BudgetExceeded("Daily USD budget exhausted")
    if usage.spent_tokens + usage.reserved_tokens + tokens > settings.daily_token_limit:
        raise BudgetExceeded("Daily token budget exhausted")
    usage.reserved_usd += amount
    usage.reserved_tokens += tokens
    call = AgentCall(
        run_id=run_id,
        step=step,
        day=day,
        kind=kind,
        provider=settings.provider,
        model=settings.embedding_model if kind == "embedding" else settings.model,
        reserved_usd=amount,
        reserved_tokens=tokens,
    )
    session.add(call)
    await session.flush()
    return call


async def settle(
    session: AsyncSession,
    call_id: int,
    settings: AgentSettings,
    input_tokens: int,
    output_tokens: int,
    response: dict[str, Any],
) -> bool:
    # Keep lock order consistent with reserve: daily row, then call row.
    initial = await session.get(AgentCall, call_id)
    if initial is None:
        raise ValueError("Unknown agent call")
    usage = await session.scalar(
        select(AgentDailyUsage).where(AgentDailyUsage.day == initial.day).with_for_update()
    )
    call = await session.scalar(select(AgentCall).where(AgentCall.id == call_id).with_for_update())
    assert call is not None and usage is not None
    if call.status in ("completed", "usage_exceeded"):
        return call.status == "completed"
    if min(input_tokens, output_tokens) < 0:
        raise ValueError("Invalid provider usage")
    rate = (
        settings.embedding_input_usd_per_million
        if call.kind == "embedding"
        else settings.input_usd_per_million
    )
    cost = price(
        input_tokens,
        output_tokens,
        rate,
        settings.output_usd_per_million,
        settings.price_safety_multiplier,
    )
    usage.reserved_usd -= call.reserved_usd
    usage.reserved_tokens -= call.reserved_tokens
    usage.spent_usd += cost
    usage.spent_tokens += input_tokens + output_tokens
    call.input_tokens, call.output_tokens, call.cost_usd = input_tokens, output_tokens, cost
    call.response = response
    bounded = cost <= call.reserved_usd and input_tokens + output_tokens <= call.reserved_tokens
    call.status = "completed" if bounded else "usage_exceeded"
    return bounded
