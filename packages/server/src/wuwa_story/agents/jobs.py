"""Idempotent job creation and explicit resumption; broker messages carry only IDs."""

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import PROMPT_VERSION, AnalysisRequest
from wuwa_story.agents.evidence import hash_value, quest_fingerprint, scope_for_request
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentJob
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
    source = await quest_fingerprint(session, quest, release.id, locale.id)
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
    session: AsyncSession, run_id: int, extra_steps: int = 0
) -> ProcessingRun:
    job = await session.get(AgentJob, run_id)
    run = await session.get(ProcessingRun, run_id)
    if job is None or run is None:
        raise ValueError("Analysis job not found")
    await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": -run_id})
    await session.refresh(run)
    if run.status not in ("paused_budget", "paused_config", "paused_steps", "enqueue_failed"):
        raise ValueError(
            "This job cannot be resumed; uncertain calls require billing reconciliation"
        )
    if extra_steps:
        job.config = {**job.config, "max_steps": min(100, job.config["max_steps"] + extra_steps)}
    run.status, run.error = "queued", None
    return await publish_job(session, run)
