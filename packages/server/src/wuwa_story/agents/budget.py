"""Atomic shared reservations before remote requests; uncertain calls stay charged."""

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall, AgentDailyUsage, AgentJob
from wuwa_story.db.models.ops import ProcessingRun


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


def reported_price(
    settings: AgentSettings,
    input_tokens: int,
    output_tokens: int,
    response: dict[str, Any],
    kind: str,
) -> Decimal:
    if kind == "embedding":
        return price(
            input_tokens,
            output_tokens,
            settings.embedding_input_usd_per_million,
            settings.output_usd_per_million,
            settings.price_safety_multiplier,
        )
    if settings.provider == "gemini":
        usage = response.get("usageMetadata", {})
        if not isinstance(usage, dict):
            raise ValueError("Invalid cache usage")
        cached = usage.get("cachedContentTokenCount", 0)
        written = 0
    else:
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("Invalid cache usage")
        details = usage.get("input_tokens_details", usage.get("prompt_tokens_details", {})) or {}
        if not isinstance(details, dict):
            raise ValueError("Invalid cache usage")
        cached, written = details.get("cached_tokens", 0), details.get("cache_write_tokens", 0)
    if (
        type(cached) is not int
        or type(written) is not int
        or min(cached, written) < 0
        or cached + written > input_tokens
    ):
        raise ValueError("Invalid cache usage")
    cached_rate = settings.cached_input_usd_per_million
    write_rate = settings.cache_write_usd_per_million
    input_cost = (
        (input_tokens - cached - written) * settings.input_usd_per_million
        + cached * (cached_rate if cached_rate is not None else settings.input_usd_per_million)
        + written * (write_rate if write_rate is not None else settings.input_usd_per_million)
    )
    return (
        (input_cost + output_tokens * settings.output_usd_per_million)
        * settings.price_safety_multiplier
        / Decimal(1_000_000)
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
        select(AgentDailyUsage)
        .where(AgentDailyUsage.day == day)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert usage is not None
    rate = (
        settings.embedding_input_usd_per_million
        if kind == "embedding"
        else max(
            settings.input_usd_per_million,
            settings.cached_input_usd_per_million or Decimal(0),
            settings.cache_write_usd_per_million or Decimal(0),
        )
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
        raise BudgetExceeded(
            f"Daily USD budget cannot cover next request: spent={usage.spent_usd}, "
            f"held={usage.reserved_usd}, requested={amount}, limit={settings.daily_budget_usd}"
        )
    if (
        settings.daily_token_limit
        and usage.spent_tokens + usage.reserved_tokens + tokens > settings.daily_token_limit
    ):
        raise BudgetExceeded(
            f"Daily token budget cannot cover next request: spent={usage.spent_tokens}, "
            f"held={usage.reserved_tokens}, requested={tokens}, limit={settings.daily_token_limit}"
        )
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
        select(AgentDailyUsage)
        .where(AgentDailyUsage.day == initial.day)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    call = await session.scalar(
        select(AgentCall)
        .where(AgentCall.id == call_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert call is not None and usage is not None
    if call.status in ("completed", "usage_exceeded"):
        return call.status == "completed"
    if min(input_tokens, output_tokens) < 0:
        raise ValueError("Invalid provider usage")
    cost = reported_price(settings, input_tokens, output_tokens, response, call.kind)
    usage.reserved_usd -= call.reserved_usd
    usage.reserved_tokens -= call.reserved_tokens
    usage.spent_usd += cost
    usage.spent_tokens += input_tokens + output_tokens
    call.input_tokens, call.output_tokens, call.cost_usd = input_tokens, output_tokens, cost
    call.response = response
    bounded = cost <= call.reserved_usd and input_tokens + output_tokens <= call.reserved_tokens
    call.status = "completed" if bounded else "usage_exceeded"
    return bounded


async def reprice_run(
    session: AsyncSession,
    run_id: int,
    cached_rate: Decimal,
    write_rate: Decimal,
) -> dict[str, Any]:
    """Explicit, atomic maintenance of known charges; never releases unknown reserves."""
    if min(cached_rate, write_rate) < 0:
        raise ValueError("Prices cannot be negative")
    locked = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": -run_id})
    if not locked:
        raise ValueError("Run is being processed; retry after it stops")
    run, job = await session.get(ProcessingRun, run_id), await session.get(AgentJob, run_id)
    if run is None or job is None or run.status in ("queued", "running"):
        raise ValueError("Select an existing inactive run")
    if job.config["provider"] != "responses":
        raise ValueError("This maintenance command requires Responses usage")
    config = {
        **job.config,
        "cached_input_usd_per_million": str(cached_rate),
        "cache_write_usd_per_million": str(write_rate),
    }
    settings = AgentSettings(_env_file=None, **config)
    calls = list(
        await session.scalars(
            select(AgentCall)
            .where(AgentCall.run_id == run_id, AgentCall.status == "completed")
            .order_by(AgentCall.day, AgentCall.id)
        )
    )
    delta = Decimal(0)
    for call in calls:
        if call.input_tokens is None or call.output_tokens is None or call.response is None:
            raise ValueError("Completed call is missing recorded usage")
        await session.flush()
        usage = await session.scalar(
            select(AgentDailyUsage)
            .where(AgentDailyUsage.day == call.day)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert usage is not None and call.cost_usd is not None
        cost = reported_price(
            settings, call.input_tokens, call.output_tokens, call.response, call.kind
        )
        difference = cost - call.cost_usd
        usage.spent_usd += difference
        if usage.spent_usd < 0:
            raise ValueError("Ledger mismatch; no repricing applied")
        response = dict(call.response)
        response.setdefault("_cost_before_cache_pricing", str(call.cost_usd))
        call.response, call.cost_usd = response, cost
        delta += difference
    run.cost = float(Decimal(str(run.cost or 0)) + delta)
    job.config = config
    return {"run_id": run_id, "calls": len(calls), "delta_usd": format(delta, ".8f")}


async def rebuild_daily_usage(session: AsyncSession) -> list[dict[str, Any]]:
    """Reconcile derived daily totals under the same locks as reserve/settle."""
    await session.flush()
    days = list(await session.scalars(select(AgentDailyUsage.day).order_by(AgentDailyUsage.day)))
    results = []
    for day in days:
        usage = await session.scalar(
            select(AgentDailyUsage)
            .where(AgentDailyUsage.day == day)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert usage is not None
        totals = (
            await session.execute(
                select(
                    func.coalesce(func.sum(AgentCall.cost_usd), 0),
                    func.coalesce(
                        func.sum(
                            case((AgentCall.cost_usd.is_(None), AgentCall.reserved_usd), else_=0)
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    AgentCall.cost_usd.is_not(None),
                                    func.coalesce(AgentCall.input_tokens, 0)
                                    + func.coalesce(AgentCall.output_tokens, 0),
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case((AgentCall.cost_usd.is_(None), AgentCall.reserved_tokens), else_=0)
                        ),
                        0,
                    ),
                ).where(AgentCall.day == day)
            )
        ).one()
        results.append(
            {
                "day": str(day),
                "previous_spent_usd": str(usage.spent_usd),
                "spent_usd": str(totals[0]),
                "reserved_usd": str(totals[1]),
            }
        )
        spent, held, spent_tokens, held_tokens = cast(tuple[Decimal, Decimal, int, int], totals)
        usage.spent_usd, usage.reserved_usd = spent, held
        usage.spent_tokens, usage.reserved_tokens = spent_tokens, held_tokens
    return results
