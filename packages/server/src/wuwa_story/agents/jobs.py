"""Idempotent job creation and explicit resumption; broker messages carry only IDs."""

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import PROMPT_VERSION, AnalysisRequest, QuestAssessment
from wuwa_story.agents.evidence import (
    hash_value,
    imported_snapshot_ids,
    imported_source_revision,
    quest_fingerprint,
    scope_for_request,
)
from wuwa_story.agents.lore import assessment_policy
from wuwa_story.agents.providers import Provider
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentCall, AgentJob, AgentRevisit
from wuwa_story.db.models.content import Document
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
    session: AsyncSession,
    request: AnalysisRequest,
    settings: AgentSettings,
    *,
    revisit: AgentRevisit | None = None,
) -> ProcessingRun:
    quest, release, locale = await scope_for_request(
        session, **request.model_dump(exclude={"generation"})
    )
    request = request.model_copy(
        update={"game_version": release.game_version, "locale": locale.code}
    )
    source = await quest_fingerprint(session, quest, release.id, include_visual=True)
    source_release_ids = await imported_snapshot_ids(session)
    source_revision = await imported_source_revision(session)
    config = settings.public_config()
    review_context = None
    checkpoint = {}
    if revisit:
        parent = await session.get(Document, revisit.document_id)
        if parent is None:
            raise ValueError("Original explanation no longer exists")
        assessment = QuestAssessment.model_validate(parent.metadata_json["assessment"])
        authored_type = parent.metadata_json.get("authored_quest_type")
        policy = assessment_policy(assessment, authored_main=authored_type == "1")
        checkpoint = {"assessment": assessment.model_dump(), "policy": policy, "stage": "revisit", "authored_quest_type": authored_type}
        config["max_steps"] = min(settings.max_steps, 8)
        config["max_output_tokens"] = max(settings.max_output_tokens, policy["output_tokens"])
        matched = {candidate["hook_key"] for candidate in revisit.candidates}
        review_context = {
            "task_id": revisit.id,
            "document_id": parent.id,
            "release_id": revisit.release_id,
            "hooks": [hook for hook in parent.metadata_json["hooks"] if hook["key"] in matched],
            "candidates": revisit.candidates,
        }
    identity = hash_value(
        {
            "request": request.model_dump(),
            "target_snapshot_id": release.id,
            "source": source.hex(),
            "config": config,
            "prompt": PROMPT_VERSION,
            "source_release_ids": source_release_ids,
            "source_revision": source_revision,
            "revisit_id": revisit.id if revisit else None,
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
        if revisit:
            revisit.run_id, revisit.status, revisit.error = existing.id, "queued", None
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
        metadata_json={
            "request": request.model_dump(),
            "source_release_ids": source_release_ids,
            "revisit": review_context,
        },
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
            checkpoint=checkpoint,
        )
    )
    if revisit:
        revisit.run_id, revisit.status, revisit.error = run.id, "queued", None
    return await publish_job(session, run)


async def resume_analysis(
    session: AsyncSession,
    run_id: int,
    extra_steps: int = 0,
    context_tokens: int | None = None,
    tool_calls_per_step: int | None = None,
    compact_context: bool = False,
    output_tokens: int | None = None,
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
    recover_output = run.status == "paused_output" or (
        run.status == "failed"
        and run.error == "Malformed provider turn; recorded usage is retained"
        and output_tokens is not None
    )
    if (
        not recover_recorded
        and not recover_output
        and run.status
        not in (
            "paused_budget",
            "paused_config",
            "paused_steps",
            "paused_context",
            "paused_rate_limit",
            "paused_provider",
            "enqueue_failed",
        )
    ):
        raise ValueError(
            "This job cannot be resumed; uncertain calls require billing reconciliation"
        )
    if (
        run.status == "paused_context"
        and not compact_context
        and (context_tokens is None or context_tokens <= job.config["max_input_tokens"])
    ):
        raise ValueError(
            "Increase context_tokens or request compact_context to resume a context pause"
        )
    config_values = dict(job.config)
    if context_tokens is not None:
        config_values["max_input_tokens"] = context_tokens
    if tool_calls_per_step is not None:
        config_values["max_tool_calls_per_step"] = tool_calls_per_step
    if output_tokens is not None:
        config_values["max_output_tokens"] = output_tokens
    if extra_steps:
        config_values["max_steps"] = min(100, config_values["max_steps"] + extra_steps)
    config = AgentSettings(_env_file=None, **config_values)
    if recover_output:
        if output_tokens is None or output_tokens <= job.config["max_output_tokens"]:
            raise ValueError("Increase output_tokens to resume an output limit pause")
        call = await session.scalar(
            select(AgentCall).where(
                AgentCall.run_id == run_id,
                AgentCall.step == job.checkpoint.get("step", 0),
                AgentCall.status == "completed",
            )
        )
        async with httpx.AsyncClient() as client:
            provider = Provider(config, client)
            if call is None or call.response is None or not provider.output_limited(call.response):
                raise ValueError("No recorded output-limited response is available to recover")
            next_step = job.checkpoint.get("step", 0) + 1
            if config.max_steps <= next_step:
                raise ValueError("Increase extra_steps to allow a new response after truncation")
            # The paid truncated turn remains in the ledger. Start a new call after
            # the last fully processed turn; never execute or resend partial items.
            history = list(job.checkpoint.get("history", []))
            history.extend(
                provider.initial(
                    "",
                    "Your previous response reached the output token limit. No partial tool calls "
                    "were executed. Continue from the saved research and return a complete, concise "
                    "tool call. The output allowance has been increased.",
                )[1:]
            )
            job.checkpoint = {**job.checkpoint, "step": next_step, "history": history}
    if run.status == "paused_steps" and config.max_steps <= job.checkpoint.get("step", 0):
        raise ValueError("Increase extra_steps to resume; at most 100 research steps are allowed")
    if compact_context:
        if config.provider != "responses":
            raise ValueError("Context compaction requires the Responses provider")
        if job.checkpoint.get("compacted_at_step") == job.checkpoint.get("step", 0):
            raise ValueError("This step was already compacted; increase context_tokens instead")
        config.context_compaction = True
    if recover_recorded and not recover_output:
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
    if compact_context:
        job.checkpoint = {**job.checkpoint, "compact_requested": True}
    job.config = config.public_config()
    run.status, run.error = "queued", None
    return await publish_job(session, run)
