"""Idempotent job creation and explicit resumption; broker messages carry only IDs."""

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import PROMPT_VERSION, AnalysisRequest
from wuwa_story.agents.evidence import hash_value, quest_fingerprint, scope_for_request
from wuwa_story.agents.providers import Provider
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall, AgentJob
from wuwa_story.db.models.ops import AIModel, ProcessingRun, Processor
from wuwa_story.ingestion.media_jobs import publish_media_job

AGENT_QUEUE = "wuwa.story-agent.v1"


async def publish_job(session: AsyncSession, run: ProcessingRun) -> ProcessingRun:
    await session.commit()
    try:
        await publish_media_job({"run_id": run.id}, f"story-agent-{run.id}", AGENT_QUEUE)
    except Exception:
        # A worker can already have received a message after a lost publisher confirmation.
        await session.refresh(run)
        if run.status == "queued":
            run.status, run.error = "enqueue_failed", "Queue confirmation failed; repeat request"
            await session.commit()
        raise
    return run


async def enqueue_analysis(
    session: AsyncSession, request: AnalysisRequest, settings: AgentSettings
) -> ProcessingRun:
    quest, release, locale = await scope_for_request(
        session, **request.model_dump(exclude={"generation"})
    )
    source = await quest_fingerprint(session, quest, release.id)
    config = settings.public_config()
    identity = hash_value(
        {
            "request": request.model_dump(),
            "source": source.hex(),
            "config": config,
            "prompt": PROMPT_VERSION,
        }
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:id)"), {"id": int.from_bytes(identity[:7], "big")}
    )
    existing = await session.scalar(
        select(ProcessingRun)
        .join(AgentJob, AgentJob.run_id == ProcessingRun.id)
        .where(AgentJob.identity_hash == identity)
    )
    if existing:
        if existing.status not in ("queued", "enqueue_failed"):
            return existing
        existing.status, existing.error = "queued", None
        return await publish_job(session, existing)
    await session.execute(
        insert(Processor)
        .values(key="story_agent", version=PROMPT_VERSION)
        .on_conflict_do_nothing(index_elements=[Processor.key])
    )
    processor_id = await session.scalar(select(Processor.id).where(Processor.key == "story_agent"))
    assert processor_id is not None
    model = await session.scalar(
        select(AIModel)
        .where(AIModel.provider == settings.provider, AIModel.model_name == settings.model)
        .limit(1)
    )
    if model is None:
        model = AIModel(provider=settings.provider, model_name=settings.model)
        session.add(model)
        await session.flush()
    run = ProcessingRun(
        processor_id=processor_id,
        model_id=model.id,
        target_node_id=quest.node_id,
        prompt_version=PROMPT_VERSION,
        input_hash=source,
        status="queued",
        metadata_json={"request": request.model_dump()},
    )
    session.add(run)
    await session.flush()
    session.add(
        AgentJob(
            run_id=run.id,
            release_id=release.id,
            locale_id=locale.id,
            identity_hash=identity,
            config=config,
        )
    )
    return await publish_job(session, run)


async def resume_analysis(
    session: AsyncSession,
    run_id: int,
    extra_steps: int = 0,
    context_tokens: int | None = None,
    tool_calls_per_step: int | None = None,
) -> ProcessingRun:
    job = await session.get(AgentJob, run_id)
    run = await session.get(ProcessingRun, run_id)
    if job is None or run is None:
        raise ValueError("Analysis job not found")
    await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": -run_id})
    await session.refresh(run)
    recover_recorded = (
        run.status == "failed"
        and run.error == "Malformed provider turn; recorded usage is retained"
        and tool_calls_per_step is not None
    )
    if not recover_recorded and run.status not in (
        "paused_budget",
        "paused_config",
        "paused_steps",
        "paused_context",
        "enqueue_failed",
    ):
        raise ValueError(
            "This job cannot be resumed; uncertain calls require billing reconciliation"
        )
    if run.status == "paused_context" and (
        context_tokens is None or context_tokens <= job.config["max_input_tokens"]
    ):
        raise ValueError("Increase context_tokens to resume a context pause")
    config_values = dict(job.config)
    if context_tokens is not None:
        config_values["max_input_tokens"] = context_tokens
    if tool_calls_per_step is not None:
        config_values["max_tool_calls_per_step"] = tool_calls_per_step
    if extra_steps:
        config_values["max_steps"] = min(100, config_values["max_steps"] + extra_steps)
    config = AgentSettings(_env_file=None, **config_values)
    if recover_recorded:
        call = await session.scalar(
            select(AgentCall).where(
                AgentCall.run_id == run_id,
                AgentCall.step == job.checkpoint.get("step", 0),
                AgentCall.status == "completed",
            )
        )
        if call is None or call.response is None:
            raise ValueError("No completed provider response is available to replay")
        try:
            async with httpx.AsyncClient() as client:
                turn = Provider(config, client).parse(call.response)
            if not turn.complete:
                raise ValueError("Recorded response is incomplete")
        except (ValueError, KeyError, TypeError, IndexError) as error:
            raise ValueError(
                "Recorded response cannot be replayed with the selected limits"
            ) from error
    job.config = config.public_config()
    run.status, run.error = "queued", None
    return await publish_job(session, run)
