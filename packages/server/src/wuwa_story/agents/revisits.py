"""Import outbox and bounded candidate lookup. A search hit is never a lore fact."""

import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.contracts import AnalysisRequest, AnalysisResult
from wuwa_story.agents.lore import distinctive_term
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import AgentRevisit
from wuwa_story.db.models.content import Document, DocumentHead
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.db.models.ops import GameRelease, ProcessingRun
from wuwa_story.db.models.search import SearchDocument

logger = logging.getLogger(__name__)
REVISIT_BATCH = 20
CANDIDATES_PER_HOOK = 6
REVISIT_POLL_SECONDS = 60


async def schedule_revisits(session: AsyncSession, release_id: int) -> None:
    """Called in the successful import transaction. No broker/model I/O here."""
    statement = (
        select(Document)
        .join(DocumentHead, DocumentHead.document_id == Document.id)
        .where(
            Document.document_type.startswith("story-explanation:"),
            Document.metadata_json["schema_version"]
            .as_string()
            .in_(["story-v5", "story-v6", "story-v7"]),
        )
        .execution_options(yield_per=100)
    )
    async for document in await session.stream_scalars(statement):
        if release_id in document.metadata_json.get("source_release_ids", []):
            continue
        if not any(
            hook.get("revisit_on_new_versions") for hook in document.metadata_json.get("hooks", [])
        ):
            continue
        await session.execute(
            insert(AgentRevisit)
            .values(document_id=document.id, release_id=release_id, status="pending", candidates=[])
            .on_conflict_do_nothing(constraint="uq_agent_revisit_source")
        )


async def find_candidates(
    session: AsyncSession, document: Document, release_id: int
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    observed = select(NodeRevision.node_id).where(NodeRevision.release_id == release_id)
    for hook in document.metadata_json.get("hooks", []):
        if not hook.get("revisit_on_new_versions"):
            continue
        hits: dict[int, dict[str, Any]] = {}
        for term in hook["search_terms"]:
            if not distinctive_term(term):
                continue
            # Use the existing GIN index, require a phrase rather than loose shared words.
            query = func.phraseto_tsquery("simple", term)
            rows = (
                await session.execute(
                    select(SearchDocument.target_node_id, SearchDocument.title)
                    .where(
                        SearchDocument.target_node_id.in_(observed),
                        SearchDocument.target_node_id != document.node_id,
                        SearchDocument.search_vector.op("@@")(query),
                    )
                    .order_by(
                        func.ts_rank_cd(SearchDocument.search_vector, query).desc(),
                        SearchDocument.target_node_id,
                    )
                    .limit(CANDIDATES_PER_HOOK)
                )
            ).all()
            for node_id, title in rows:
                if node_id not in hits and len(hits) >= CANDIDATES_PER_HOOK:
                    continue
                match = hits.setdefault(
                    node_id,
                    {
                        "hook_key": hook["key"],
                        "node_id": node_id,
                        "snapshot_id": release_id,
                        "label": title[:200],
                        "terms": [],
                    },
                )
                if term not in match["terms"]:
                    match["terms"].append(term)
        candidates.extend(hits.values())
    return candidates


def validate_revisit(result: AnalysisResult, context: dict[str, Any]) -> None:
    hooks = {hook["key"]: hook for hook in context["hooks"]}
    if {review.hook_key for review in result.revisited_hooks} != set(hooks) or len(
        result.revisited_hooks
    ) != len(hooks):
        raise ValueError("Review every requested hook exactly once")
    for review in result.revisited_hooks:
        old_sources = {
            (c["snapshot_id"], c["node_id"]) for c in hooks[review.hook_key]["citations"]
        }
        sources = {(c.snapshot_id, c.node_id) for c in review.citations}
        if not sources.intersection(old_sources):
            raise ValueError("A hook review must reread and cite its original evidence")
        if not any(c.snapshot_id == context["release_id"] for c in review.citations):
            raise ValueError(
                "A hook review must cite inspected new-import evidence, even for a rejected candidate"
            )
    if result.hooks:
        raise ValueError(
            "A focused review supplements existing hooks; do not replace the original hook list"
        )


async def dispatch_revisits(
    session: AsyncSession, settings: AgentSettings, *, after: int = 0
) -> int:
    # Local import avoids making the importer's dependency graph depend on providers.
    from wuwa_story.agents.jobs import enqueue_analysis, publish_job

    statement = (
        select(AgentRevisit.id)
        .outerjoin(ProcessingRun, ProcessingRun.id == AgentRevisit.run_id)
        .where(
            AgentRevisit.id > after,
            (AgentRevisit.status == "pending")
            | (
                (AgentRevisit.status == "queued")
                & ProcessingRun.status.in_(["queued", "enqueue_failed"])
            ),
        )
        .order_by(AgentRevisit.id)
        .limit(REVISIT_BATCH)
    )
    ids = list(await session.scalars(statement))
    for task_id in ids:
        try:
            locked = await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": -1_000_000_000_000 - task_id},
            )
            if not locked:
                await session.rollback()
                continue
            task = await session.get(AgentRevisit, task_id, populate_existing=True)
            assert task is not None
            if task.run_id:
                run = await session.get(ProcessingRun, task.run_id, populate_existing=True)
                if run and run.status in ("queued", "enqueue_failed"):
                    task.error = None
                    run.status = "queued"
                    await publish_job(session, run)
                else:
                    await session.commit()
                continue
            document = await session.get(Document, task.document_id)
            assert document is not None
            current = await session.scalar(
                select(DocumentHead.document_id).where(DocumentHead.document_id == document.id)
            )
            if not current:
                task.status = "superseded"
                await session.commit()
                continue
            task.candidates = await find_candidates(session, document, task.release_id)
            if not task.candidates:
                task.status = "no_candidates"
                await session.commit()
                continue
            settings.require_enabled()
            quest = await session.get(Quest, document.node_id)
            release = await session.get(GameRelease, document.metadata_json["release_id"])
            assert quest is not None and release is not None
            await enqueue_analysis(
                session,
                AnalysisRequest(quest_id=quest.game_quest_id, game_version=release.game_version),
                settings,
                revisit=task,
            )
        except Exception as error:
            await session.rollback()
            task = await session.get(AgentRevisit, task_id, populate_existing=True)
            if task:
                task.error = f"{type(error).__name__}: review dispatch deferred; retry is automatic"
                await session.commit()
            logger.warning(
                "agent.revisit_deferred task_id=%s error_type=%s", task_id, type(error).__name__
            )
    return ids[-1] if ids else 0
