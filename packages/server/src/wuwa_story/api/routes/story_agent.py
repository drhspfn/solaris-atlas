"""Admin orchestration and public cited explanations; never expose native model reasoning."""

from datetime import UTC, datetime
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.budget import settle
from wuwa_story.agents.contracts import AnalysisRequest, StrictModel
from wuwa_story.agents.jobs import enqueue_analysis, resume_analysis
from wuwa_story.agents.providers import Provider
from wuwa_story.agents.retrieval import (
    get_connection,
    get_explanation,
    query_vector,
    scope,
    search_explanations,
)
from wuwa_story.agents.settings import AgentSettings, get_agent_settings
from wuwa_story.agents.trace import legacy_trace
from wuwa_story.api.routes.cutscene_analysis import router as cutscene_admin
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models.agents import (
    AgentCall,
    AgentDailyUsage,
    AgentJob,
    AgentNote,
    AgentRevisit,
)
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.models.story import Event
from wuwa_story.db.session import get_session

router = APIRouter(tags=["story explanations"])
admin = APIRouter(
    prefix="/admin/story-agent", tags=["story agent"], dependencies=[Depends(require_admin)]
)
admin.include_router(cutscene_admin)


class ResumeRequest(StrictModel):
    finalize: bool = False
    extra_steps: int = Field(default=0, ge=0, le=100)
    context_tokens: int | None = Field(default=None, ge=1000, le=250000)
    tool_calls_per_step: int | None = Field(default=None, ge=1, le=20)
    compact_context: bool = False
    output_tokens: int | None = Field(default=None, ge=128, le=32000)


class ReconcileRequest(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class AdminAnalysisRequest(AnalysisRequest):
    locale: Literal["en"] = "en"


@admin.get("/revisits")
async def revisit_jobs(
    before: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    statement = (
        select(AgentRevisit, ProcessingRun.status)
        .outerjoin(ProcessingRun, ProcessingRun.id == AgentRevisit.run_id)
        .order_by(AgentRevisit.id.desc())
        .limit(30)
    )
    if before:
        statement = statement.where(AgentRevisit.id < before)
    rows = (await session.execute(statement)).all()
    return {
        "revisits": [
            {
                "id": task.id,
                "document_id": task.document_id,
                "release_id": task.release_id,
                "status": status or task.status,
                "run_id": task.run_id,
                "candidate_count": len(task.candidates),
                "error": task.error,
            }
            for task, status in rows
        ],
        "next_before": rows[-1][0].id if len(rows) == 30 else None,
    }


@admin.post("/jobs", status_code=202, dependencies=[Depends(require_csrf)])
async def create_job(
    request: AdminAnalysisRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        run = await enqueue_analysis(session, request, get_agent_settings())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(503, "Story queue unavailable; repeat the same request") from error
    return {"id": run.id, "status": run.status}


@admin.get("/jobs")
async def jobs(
    limit: int = Query(30, ge=1, le=100),
    before: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    statement = (
        select(ProcessingRun, AgentJob.config, AgentJob.checkpoint["step"].as_integer())
        .join(AgentJob, AgentJob.run_id == ProcessingRun.id)
        .order_by(ProcessingRun.id.desc())
        .limit(limit)
    )
    if before:
        statement = statement.where(ProcessingRun.id < before)
    rows = cast(
        list[tuple[ProcessingRun, dict[str, Any], int | None]],
        list((await session.execute(statement)).tuples().all()),
    )
    return {
        "jobs": [
            {
                "id": run.id,
                "status": run.status,
                "request": run.metadata_json.get("request"),
                "mode": "revisit" if run.metadata_json.get("revisit") else "analysis",
                "error": run.error,
                "tokens_input": run.tokens_input,
                "tokens_output": run.tokens_output,
                "cost_usd": run.cost,
                "step": step or 0,
                "max_steps": config["max_steps"],
                "model": config["model"],
            }
            for run, config, step in rows
        ],
        "next_before": rows[-1][0].id if len(rows) == limit else None,
    }


@admin.get("/jobs/{run_id}")
async def job_status(run_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    job = await session.get(AgentJob, run_id)
    run = await session.get(ProcessingRun, run_id)
    if job is None or run is None:
        raise HTTPException(404, "Story analysis job not found")
    calls = list(
        await session.scalars(
            select(AgentCall).where(AgentCall.run_id == run_id).order_by(AgentCall.step)
        )
    )
    recovery = None
    if run.status == "failed" and run.error == "Malformed provider turn; recorded usage is retained":
        recorded = next((call for call in calls if call.step == job.checkpoint.get("step", 0) and call.status == "completed" and call.kind == "analysis"), None)
        if recorded and recorded.response:
            try:
                async with httpx.AsyncClient() as client:
                    parsed = Provider(AgentSettings(_env_file=None, **job.config), client).parse(recorded.response, enforce_tool_limit=False)
                if parsed.complete and parsed.calls:
                    recovery = "recorded_tools"
            except (ValueError, KeyError, TypeError, IndexError):
                pass
    return {
        "id": run.id,
        "recovery": recovery,
        "status": run.status,
        "step": job.checkpoint.get("step", 0),
        "document_id": job.document_id,
        "request": run.metadata_json.get("request"),
        "result": run.raw_output,
        "assessment": job.checkpoint.get("assessment"),
        "policy": job.checkpoint.get("policy"),
        "stage": job.checkpoint.get("stage"),
        "finalization_end": job.checkpoint.get("finalization_end"),
        "revisit": run.metadata_json.get("revisit"),
        "error": run.error,
        "cost_usd": run.cost,
        "tokens_input": run.tokens_input,
        "tokens_output": run.tokens_output,
        "limits": {
            key: job.config.get(key)
            for key in (
                "max_steps",
                "max_input_tokens",
                "max_output_tokens",
                "max_tool_calls_per_step",
                "provider",
                "model",
            )
        },
        "calls": [
            {
                "id": call.id,
                "step": call.step,
                "kind": call.kind,
                "status": call.status,
                "model": call.model,
                "reserved_usd": str(call.reserved_usd),
                "cost_usd": str(call.cost_usd) if call.cost_usd is not None else None,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "tool_errors": sum(1 for item in (call.response or {}).get("execution_trace", {}).get("tools", [])
                                   if item.get("status") == "error"),
                "repeated_tools": sum(1 for item in (call.response or {}).get("execution_trace", {}).get("tools", [])
                                      if item.get("repeat_count", 0) > 0),
                "provider_error": (call.response or {}).get("provider_error")
                if call.status in ("rate_limited", "provider_rejected")
                else None,
                "retry_at": (call.response or {}).get("retry_at")
                if call.status == "rate_limited"
                else None,
            }
            for call in calls
        ],
    }


@admin.get("/jobs/{run_id}/calls/{call_id}")
async def call_trace(run_id: int, call_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    call = await session.get(AgentCall, call_id)
    if call is None or call.run_id != run_id:
        raise HTTPException(404, "Request not found in this run")
    raw = call.response or {}
    trace = raw.get("execution_trace")
    if trace is None:
        # Compact output contains old conversation messages, not a new model turn.
        trace = legacy_trace(raw) if call.kind == "analysis" else {
            "recorded": False, "text": "", "tools": []}
    return {"id": call.id, "created_at": call.created_at, **trace}


@admin.post("/jobs/{run_id}/resume", dependencies=[Depends(require_csrf)])
async def resume(
    run_id: int, request: ResumeRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    try:
        run = await resume_analysis(
            session,
            run_id,
            request.extra_steps,
            request.context_tokens,
            request.tool_calls_per_step,
            request.compact_context,
            request.output_tokens,
            request.finalize,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(503, "Story queue unavailable") from error
    return {"id": run.id, "status": run.status}


@admin.post("/calls/{call_id}/reconcile", dependencies=[Depends(require_csrf)])
async def reconcile(
    call_id: int, request: ReconcileRequest, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    call = await session.get(AgentCall, call_id)
    if call is None:
        raise HTTPException(404, "Agent call not found")
    if call.run_id:
        await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": -call.run_id})
    await session.refresh(call)
    if call.status not in ("uncertain", "reserved"):
        raise HTTPException(409, "Only an unresolved charge can be reconciled")
    live = get_agent_settings()
    job = await session.get(AgentJob, call.run_id) if call.run_id else None
    pinned = AgentSettings(_env_file=None, **job.config) if job else live
    await settle(
        session,
        call.id,
        pinned,
        request.input_tokens,
        request.output_tokens,
        {"billing_reconciled": True},
    )
    call.status = "reconciled"
    if call.run_id:
        run = await session.get(ProcessingRun, call.run_id)
        if run:
            run.status, run.error = (
                "failed",
                "Billing reconciled; remote result unavailable. Start an explicit new generation to analyze again.",
            )
            run.tokens_input = (run.tokens_input or 0) + request.input_tokens
            run.tokens_output = (run.tokens_output or 0) + request.output_tokens
            run.cost = (run.cost or 0) + float(call.cost_usd or 0)
    await session.commit()
    return {"id": call.id, "status": call.status}


@admin.get("/usage")
async def usage(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    days = list(
        await session.scalars(
            select(AgentDailyUsage).order_by(AgentDailyUsage.day.desc()).limit(31)
        )
    )
    settings = get_agent_settings()
    return {
        "daily_budget_usd": str(settings.daily_budget_usd),
        "daily_token_limit": settings.daily_token_limit,
        "timezone": settings.budget_timezone,
        "today": str(datetime.now(UTC).astimezone(ZoneInfo(settings.budget_timezone)).date()),
        "days": [
            {
                "day": str(day.day),
                "spent_usd": str(day.spent_usd),
                "reserved_usd": str(day.reserved_usd),
                "spent_tokens": day.spent_tokens,
                "reserved_tokens": day.reserved_tokens,
            }
            for day in days
        ],
    }


@admin.get("/alerts")
async def alerts(
    limit: int = Query(50, ge=1, le=100),
    before: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    statement = (
        select(AgentNote)
        .where(AgentNote.kind != "note", AgentNote.status == "open")
        .order_by(AgentNote.id.desc())
        .limit(limit)
    )
    if before:
        statement = statement.where(AgentNote.id < before)
    notes = list(await session.scalars(statement))
    return {
        "alerts": [
            {
                "id": note.id,
                "run_id": note.run_id,
                "kind": note.kind,
                "text": note.text,
                "node_id": note.node_id,
                "citations": note.citations,
            }
            for note in notes
        ],
        "next_before": notes[-1].id if len(notes) == limit else None,
    }


@admin.post("/alerts/{note_id}/resolve", dependencies=[Depends(require_csrf)])
async def resolve_alert(
    note_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    note = await session.get(AgentNote, note_id)
    if note is None:
        raise HTTPException(404, "Alert not found")
    note.status = "resolved"
    await session.commit()
    return {"id": note.id, "status": note.status}


@router.get("/quests/{quest_id}/explanation")
async def explanation(
    quest_id: int,
    game_version: str | None = None,
    locale: str = "en",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        return await get_explanation(session, quest_id, game_version, locale)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/story-analysis/connections/{document_id}/{index}")
async def connection(
    document_id: int,
    index: int,
    locale: str = "en",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        return await get_connection(session, document_id, index, locale)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/story-analysis/search")
async def question_search(
    request: Request,
    q: str = Query(min_length=1, max_length=512),
    game_version: str | None = None,
    locale: str = "en",
    limit: int = Query(8, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    settings = get_agent_settings()
    try:
        await scope(session, game_version, locale)
        vector = await query_vector(
            session,
            q,
            settings,
            getattr(request.app.state, "agent_query_redis", None),
            request.client.host if request.client else "unknown",
        )
        return await search_explanations(session, q, game_version, locale, limit, vector, settings)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/story-analysis/events/{node_id}")
async def generated_event(
    node_id: int,
    game_version: str | None = None,
    locale: str = "en",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        release, language = await scope(session, game_version, locale)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    event = await session.get(Event, node_id)
    if (
        event is None
        or event.semantic_status != "generated"
        or event.metadata_json.get("release_id") != release.id
    ):
        raise HTTPException(404, "Generated event not found in this version/language")
    run = await session.get(ProcessingRun, event.processor_run_id)
    quest_id = run.metadata_json.get("request", {}).get("quest_id") if run else None
    current = await get_explanation(session, quest_id, game_version, locale) if quest_id else None
    if (
        not current
        or not current["explanation"]
        or node_id not in [item["node_id"] for item in current["explanation"]["events"]]
    ):
        raise HTTPException(404, "Event interpretation has been superseded")
    return {
        "node_id": node_id,
        "title": event.title,
        "description": event.metadata_json.get("description"),
        "citations": event.metadata_json.get("citations", []),
        "generated": True,
        "quest_id": quest_id,
        "game_version": release.game_version,
    }


class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(StrictModel):
    messages: list[ChatMessage]


@admin.post("/chat", dependencies=[Depends(require_csrf)])
async def admin_chat(request: ChatRequest, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    from wuwa_story.agents.providers import Provider
    from wuwa_story.agents.settings import get_agent_settings
    import httpx

    settings = get_agent_settings()
    
    tools = [
        {
            "name": "search_lore",
            "description": "Semantic search across previously generated Wuthering Waves storyline lore (cutscenes, quests, characters). Use this to recall past lore facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"],
                "additionalProperties": False,
            }
        },
        {
            "name": "get_quest_lore",
            "description": "Get all generated lore facts and cutscenes for a specific quest ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "quest_id": {"type": "string"}
                },
                "required": ["quest_id"],
                "additionalProperties": False,
            }
        },
        {
            "name": "update_lore_chunk",
            "description": "Correct or update an existing lore chunk if you find a mistake.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "integer"},
                    "new_content": {"type": "string"}
                },
                "required": ["chunk_id", "new_content"],
                "additionalProperties": False,
            }
        },
        {
            "name": "add_lore_fact",
            "description": "Add a brand new lore fact or explanation to the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "quest_id": {"type": "string", "description": "Optional quest ID this relates to"},
                    "characters": {"type": "array", "items": {"type": "string"}, "description": "Characters involved"}
                },
                "required": ["content"],
                "additionalProperties": False,
            }
        }
    ]

    history = [
        {"role": "system", "content": "You are a Lore Assistant. You help the admin answer lore questions by searching past generated lore explanations using the search_lore tool. You can read entire quests with get_quest_lore. If you notice inaccuracies or want to add explicit manual context, use update_lore_chunk or add_lore_fact."}
    ]
    for msg in request.messages:
        history.append({"role": msg.role, "content": msg.content})

    async with httpx.AsyncClient() as client:
        provider = Provider(settings, client)
        
        for _ in range(5):
            path, payload = provider.request(history, tools)
            raw = await provider.post(path, payload)
            turn = provider.parse(raw)
            
            if turn.calls:
                # Add model's tool calls to history
                history.extend(turn.items)
                
                # Execute tools and append responses
                for call in turn.calls:
                    if call.name == "search_lore":
                        query = call.arguments.get("query", "")
                        from wuwa_story.search.lore import LoreSearchService
                        from wuwa_story.search.embedding import generate_query_embedding
                        try:
                            query_embedding = await generate_query_embedding(session, query)
                            service = LoreSearchService(session)
                            results = await service.hybrid_search(query, query_embedding, limit=5)
                            output = ""
                            for i, r in enumerate(results, 1):
                                output += f"[{i}] Chunk ID: {r.id} | Quest: {r.quest_id} | Type: {r.chunk_type}\n{r.content}\n\n"
                            result_data = {"results": output if output else "No results found"}
                        except Exception as e:
                            result_data = {"error": str(e)}
                    elif call.name == "get_quest_lore":
                        quest_id = call.arguments.get("quest_id")
                        from sqlalchemy import select
                        from wuwa_story.db.models.lore import LoreChunk
                        try:
                            stmt = select(LoreChunk).where(LoreChunk.quest_id == quest_id).order_by(LoreChunk.id)
                            result = await session.execute(stmt)
                            chunks = result.scalars().all()
                            if chunks:
                                output = ""
                                for r in chunks:
                                    output += f"[Chunk ID: {r.id}] Type: {r.chunk_type}\n{r.content}\n\n"
                                result_data = {"results": output}
                            else:
                                result_data = {"results": "No lore found for this quest."}
                        except Exception as e:
                            result_data = {"error": str(e)}
                    elif call.name == "update_lore_chunk":
                        chunk_id = call.arguments.get("chunk_id")
                        new_content = call.arguments.get("new_content")
                        from wuwa_story.db.models.lore import LoreChunk, LoreChunkEmbedding
                        from wuwa_story.search.embedding import generate_query_embedding
                        from sqlalchemy import select, delete, func
                        import hashlib
                        try:
                            chunk = await session.get(LoreChunk, chunk_id)
                            if chunk:
                                chunk.content = new_content
                                chunk.search_vector = func.to_tsvector("english", new_content)
                                await session.execute(delete(LoreChunkEmbedding).where(LoreChunkEmbedding.chunk_id == chunk_id))
                                new_emb = await generate_query_embedding(session, new_content)
                                from wuwa_story.db.models.search import EmbeddingModel
                                model = await session.scalar(select(EmbeddingModel).where(EmbeddingModel.active == True).limit(1))
                                if model:
                                    content_hash = hashlib.sha256(new_content.encode()).digest()
                                    new_emb_row = LoreChunkEmbedding(
                                        chunk_id=chunk_id,
                                        model_id=model.id,
                                        content_hash=content_hash,
                                        embedding=new_emb
                                    )
                                    session.add(new_emb_row)
                                await session.commit()
                                result_data = {"success": True, "message": f"Chunk {chunk_id} updated."}
                            else:
                                result_data = {"error": "Chunk not found."}
                        except Exception as e:
                            await session.rollback()
                            result_data = {"error": str(e)}
                    elif call.name == "add_lore_fact":
                        content = call.arguments.get("content")
                        quest_id = call.arguments.get("quest_id")
                        characters = call.arguments.get("characters", [])
                        from wuwa_story.db.models.lore import LoreChunk, LoreChunkEmbedding
                        from wuwa_story.search.embedding import generate_query_embedding
                        from wuwa_story.db.models.search import EmbeddingModel
                        from sqlalchemy import select, func
                        import hashlib
                        try:
                            new_chunk = LoreChunk(
                                source_type="manual_fact",
                                source_id="admin_chat",
                                quest_id=quest_id,
                                characters=characters,
                                chunk_type="fact",
                                content=content,
                                search_vector=func.to_tsvector("english", content)
                            )
                            session.add(new_chunk)
                            await session.flush()
                            
                            new_emb = await generate_query_embedding(session, content)
                            model = await session.scalar(select(EmbeddingModel).where(EmbeddingModel.active == True).limit(1))
                            if model:
                                content_hash = hashlib.sha256(content.encode()).digest()
                                new_emb_row = LoreChunkEmbedding(
                                    chunk_id=new_chunk.id,
                                    model_id=model.id,
                                    content_hash=content_hash,
                                    embedding=new_emb
                                )
                                session.add(new_emb_row)
                            await session.commit()
                            result_data = {"success": True, "message": f"New fact added with chunk ID {new_chunk.id}."}
                        except Exception as e:
                            await session.rollback()
                            result_data = {"error": str(e)}
                    else:
                        result_data = {"error": "Unknown tool"}
                        
                    history.append(provider.tool_result(call, result_data))
            else:
                return {"reply": turn.text}
                
        return {"reply": "I needed too many steps to answer. Please try again."}

