"""Searchable questions and bounded autonomous dispatch through existing jobs."""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import AnalysisRequest, AnalysisResult
from wuwa_story.agents.evidence import EvidenceTools, hash_value, imported_source_revision
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agent_workspace import AgentWorkItem
from wuwa_story.db.models.agents import AgentDailyUsage, AgentJob
from wuwa_story.db.models.content import Document, DocumentHead
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.ops import ProcessingRun

PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "flavor": 4}


async def publish_questions(
    session: AsyncSession, document: Document, result: AnalysisResult
) -> None:
    questions = [
        (
            hook.key,
            hook.question,
            PRIORITY[hook.priority],
            hook.search_terms,
            [citation.model_dump() for citation in hook.citations],
        )
        for hook in result.hooks
    ]
    described = {question.casefold() for _, question, _, _, _ in questions}
    questions += [
        ("question-" + hash_value(question.casefold()).hex()[:20], question, 2, [], [])
        for question in result.unresolved_questions
        if question.casefold() not in described
    ]
    for key, question, priority, terms, citations in questions:
        values = dict(
            document_id=document.id,
            title=question[:512],
            text=question,
            priority=priority,
            search_terms=terms,
            citations=citations,
            updated_at=datetime.now(UTC),
        )
        await session.execute(
            insert(AgentWorkItem)
            .values(
                key=f"question:{document.node_id}:{document.locale_id}:{document.metadata_json['release_id']}:{key}",
                kind="question",
                node_id=document.node_id,
                status="open",
                revision=1,
                resolution={},
                **values,
            )
            .on_conflict_do_update(
                index_elements=[AgentWorkItem.key],
                set_={**values, "revision": AgentWorkItem.revision + 1},
            )
        )


async def dispatch_work(session: AsyncSession, settings: AgentSettings) -> int | None:
    if not settings.autonomous_enabled:
        return None
    settings.require_enabled()
    locked = await session.scalar(text("SELECT pg_try_advisory_xact_lock(705, 80)"))
    if not locked:
        await session.rollback()
        return None
    from wuwa_story.agents.jobs import publish_job

    failed_queue = await session.scalar(
        select(ProcessingRun)
        .join(AgentWorkItem, AgentWorkItem.run_id == ProcessingRun.id)
        .where(AgentWorkItem.status == "queued", ProcessingRun.status == "enqueue_failed")
        .limit(1)
    )
    if failed_queue is not None:
        failed_queue.status = "queued"
        await publish_job(session, failed_queue)
        return failed_queue.id
    pending = await session.scalar(
        select(func.count())
        .select_from(AgentJob)
        .join(ProcessingRun, ProcessingRun.id == AgentJob.run_id)
        .where(ProcessingRun.status.in_(["queued", "running", "enqueue_failed"]))
    )
    day = datetime.now(UTC).astimezone(ZoneInfo(settings.budget_timezone)).date()
    usage = await session.get(AgentDailyUsage, day)
    if (
        (pending or 0) >= settings.autonomous_max_pending
        or usage
        and (
            usage.spent_usd + usage.reserved_usd >= settings.daily_budget_usd
            or settings.daily_token_limit
            and usage.spent_tokens + usage.reserved_tokens >= settings.daily_token_limit
        )
    ):
        await session.rollback()
        return None
    corpus = await imported_source_revision(session)
    # Finish acknowledged work; never automatically resume failed or uncertain executions.
    queued = await session.execute(
        select(AgentWorkItem, ProcessingRun.status)
        .join(ProcessingRun, ProcessingRun.id == AgentWorkItem.run_id)
        .where(AgentWorkItem.status == "queued")
        .with_for_update(of=AgentWorkItem, skip_locked=True)
    )
    for item, status in queued:
        if status == "completed":
            item.status = "completed" if item.kind == "analysis" else "open"
        elif status not in ("queued", "running"):
            item.status = "open" if item.kind == "question" else "deferred"
    active_head = (
        select(DocumentHead.document_id)
        .where(DocumentHead.document_id == AgentWorkItem.document_id)
        .exists()
    )
    task = await session.scalar(
        select(AgentWorkItem)
        .where(
            AgentWorkItem.status.in_(["open", "partial"]),
            or_(AgentWorkItem.attempted_corpus.is_(None), AgentWorkItem.attempted_corpus != corpus),
            or_(AgentWorkItem.document_id.is_(None), active_head),
        )
        .order_by(AgentWorkItem.priority, AgentWorkItem.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if task is None:
        # Choose imported quests with no published explanation or prior agent job.
        has_job = (
            select(ProcessingRun.id)
            .join(AgentJob, AgentJob.run_id == ProcessingRun.id)
            .where(ProcessingRun.target_node_id == Quest.node_id)
            .exists()
        )
        has_head = (
            select(DocumentHead.id)
            .where(
                DocumentHead.node_id == Quest.node_id,
                DocumentHead.document_type.startswith("story-explanation:"),
            )
            .exists()
        )
        observed = select(NodeRevision.id).where(NodeRevision.node_id == Quest.node_id).exists()
        target = await session.scalar(
            select(Quest)
            .where(~has_job, ~has_head, observed)
            .order_by(Quest.game_quest_id)
            .limit(1)
        )
        if target is None:
            await session.commit()
            return None
        task = AgentWorkItem(
            key=f"autonomous-analysis:{target.node_id}",
            kind="analysis",
            node_id=target.node_id,
            title=f"Analyze quest {target.game_quest_id}",
            text="No published explanation exists",
            status="open",
            priority=2,
            search_terms=[],
            citations=[],
            resolution={},
            revision=1,
        )
        session.add(task)
        await session.flush()
    quest = await session.get(Quest, task.node_id)
    if quest is None:
        task.status = "deferred"
        await session.commit()
        return None
    from wuwa_story.agents.jobs import enqueue_analysis

    task.attempted_corpus = corpus
    # Enqueue's durable identity and queue-confirmation recovery also cover this dispatcher.
    run = await enqueue_analysis(
        session,
        AnalysisRequest(quest_id=quest.game_quest_id, generation=f"work-{task.id}-{corpus[:16]}"),
        settings,
        work_item=task,
    )
    task.run_id, task.status = run.id, "queued"
    await session.commit()
    return run.id


async def public_questions(
    session: AsyncSession, document_id: int, tools: EvidenceTools
) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(AgentWorkItem)
        .where(AgentWorkItem.document_id == document_id, AgentWorkItem.kind == "question")
        .order_by(AgentWorkItem.priority, AgentWorkItem.id)
        .limit(42)
    )
    output = []
    from wuwa_story.agents.evidence import EvidenceTools, imported_snapshot_ids

    sources = await imported_snapshot_ids(session)
    resolution_tools = EvidenceTools(
        session,
        run_id=tools.run_id,
        quest=tools.quest,
        release_id=tools.release_id,
        locale=tools.locale,
        settings=tools.settings,
        source_release_ids=sources,
    )
    for row in rows:
        resolution = row.resolution or None
        if resolution:
            run = await session.get(ProcessingRun, resolution["run_id"])
            valid = run is not None and run.status == "completed"
            verified_citations = []
            for citation in resolution.get("citations", []):
                if not valid:
                    break
                try:
                    context = await resolution_tools.context(
                        citation.get("snapshot_id") or tools.release_id
                    )
                    node = await context.read_node(citation["node_id"], citation.get("locale"))
                    valid = citation["quote"] in node["text"]
                    from urllib.parse import quote

                    verified_citations.append(
                        {**citation, "href": "/nodes/" + quote(node["canonical_key"], safe="")}
                    )
                except ValueError:
                    valid = False
            if not valid:
                resolution = None
            else:
                resolution = {**resolution, "citations": verified_citations}
        output.append(
            {
                "id": row.id,
                "question": row.text,
                "priority": row.priority,
                "status": row.status if resolution else "open",
                "resolution": resolution,
            }
        )
    return output
