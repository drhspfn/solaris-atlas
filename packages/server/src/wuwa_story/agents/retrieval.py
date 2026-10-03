"""Readable explanation retrieval; paid query vectors are separately guarded and cached."""

import json
from collections.abc import Awaitable
from typing import Any, cast

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.budget import BudgetExceeded, reserve, settle
from wuwa_story.agents.evidence import hash_value, quest_fingerprint
from wuwa_story.agents.providers import Provider, vector_values
from wuwa_story.agents.publication import document_type
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.agents import ExplanationEmbedding
from wuwa_story.db.models.content import Document, DocumentHead, DocumentReference
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.search import EmbeddingModel


async def scope(
    session: AsyncSession, game_version: str | None, locale: str
) -> tuple[GameRelease, Locale]:
    stmt = select(GameRelease).order_by(GameRelease.sequence.desc()).limit(1)
    if game_version:
        stmt = stmt.where(GameRelease.game_version == game_version)
    release = await session.scalar(stmt)
    language = await session.scalar(select(Locale).where(Locale.code == locale))
    if release is None or language is None:
        raise ValueError("Imported version or locale not found")
    return release, language


async def public_document(
    session: AsyncSession, document: Document, quest: Quest, release: GameRelease, locale: Locale
) -> dict[str, Any] | None:
    if document.source_hash != await quest_fingerprint(session, quest, release.id, locale.id):
        return None
    events = (
        await session.execute(
            select(DocumentReference.target_node_id, DocumentReference.label).where(
                DocumentReference.document_id == document.id,
                DocumentReference.reference_type == "event",
            )
        )
    ).all()
    return {
        "id": document.id,
        "quest_id": quest.game_quest_id,
        "game_version": release.game_version,
        "locale": locale.code,
        "title": document.title,
        "blocks": document.body_ast,
        "generated": True,
        "unresolved_questions": document.metadata_json.get("unresolved_questions", []),
        "events": [{"node_id": node_id, "title": title} for node_id, title in events],
        "links": document.metadata_json.get("links", []),
    }


async def get_explanation(
    session: AsyncSession, quest_id: int, game_version: str | None, locale: str
) -> dict[str, Any]:
    release, language = await scope(session, game_version, locale)
    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == quest_id))
    if quest is None:
        raise ValueError("Quest not found")
    document = await session.scalar(
        select(Document)
        .join(DocumentHead, DocumentHead.document_id == Document.id)
        .where(
            Document.node_id == quest.node_id,
            Document.locale_id == language.id,
            Document.document_type == document_type(release.id),
        )
    )
    payload = (
        await public_document(session, document, quest, release, language) if document else None
    )
    return {
        "status": "available" if payload else "pending",
        "explanation": payload,
        "game_version": release.game_version,
    }


async def query_vector(
    session: AsyncSession, query: str, settings: AgentSettings, redis: Redis | None, actor: str
) -> list[float] | None:
    if not settings.public_query_embeddings or not settings.embedding_model or redis is None:
        return None
    identity = hash_value(
        {
            "q": query,
            "provider": settings.provider,
            "url": settings.base_url,
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
        }
    ).hex()
    key = "agent:query-vector:" + identity
    try:
        cached = await redis.get(key)
        if cached:
            return vector_values(json.loads(cached), settings.embedding_dimensions)
        limiter = "agent:query-limit:" + hash_value(actor).hex()
        count = await cast(
            Awaitable[Any],
            redis.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],3600) end; return n",
                1,
                limiter,
            ),
        )
        if int(count) > settings.query_requests_per_hour:
            return None
        locked = await redis.set(
            key + ":lock", "1", nx=True, ex=int(settings.http_timeout_seconds) + 30
        )
        if not locked:
            return None
    except (RedisError, ValueError, TypeError):
        return None  # Fail closed to paid work when cache/rate limiter is unavailable.
    try:
        call = await reserve(
            session,
            settings,
            run_id=None,
            step=0,
            input_bound=len(query.encode()) + 1024,
            output_bound=0,
            kind="embedding",
        )
        await session.commit()
    except (BudgetExceeded, ValueError):
        await session.rollback()
        return None
    try:
        async with httpx.AsyncClient() as client:
            vectors, raw = await Provider(settings, client).embed([query])
        tokens = raw.get("usage", {}).get("prompt_tokens", len(query.encode()) + 1024)
        if type(tokens) is not int:
            raise ValueError("Invalid usage")
        bounded = await settle(session, call.id, settings, tokens, 0, raw)
        await session.commit()
        if not bounded:
            return None
        try:
            await redis.set(key, json.dumps(vectors[0]), ex=86400)
        except RedisError:
            pass
        return vectors[0]
    except (ValueError, KeyError, TypeError, IndexError):
        call.status = "uncertain"
        await session.commit()
        return None


async def search_explanations(
    session: AsyncSession,
    query: str,
    game_version: str | None,
    locale: str,
    limit: int,
    vector: list[float] | None = None,
    settings: AgentSettings | None = None,
) -> dict[str, Any]:
    release, language = await scope(session, game_version, locale)
    # Restrict to current published heads; older generated revisions are never search results.
    base = (
        select(Document, Quest)
        .join(DocumentHead, DocumentHead.document_id == Document.id)
        .join(Quest, Quest.node_id == Document.node_id)
        .where(
            Document.locale_id == language.id, Document.document_type == document_type(release.id)
        )
    )
    score = func.ts_rank_cd(
        func.to_tsvector("simple", Document.plain_text), func.plainto_tsquery("simple", query)
    )
    mode = "text"
    if vector is not None and settings is not None:
        model_id = await session.scalar(
            select(EmbeddingModel.id)
            .where(
                EmbeddingModel.provider == settings.provider,
                EmbeddingModel.model_name == settings.embedding_model,
                EmbeddingModel.dimensions == settings.embedding_dimensions,
                EmbeddingModel.config["base_url"].astext == settings.base_url,
            )
            .limit(1)
        )
        if model_id:
            distance = ExplanationEmbedding.embedding.cosine_distance(vector)
            statement = (
                base.add_columns(ExplanationEmbedding.ordinal, (1 - distance).label("score"))
                .join(ExplanationEmbedding, ExplanationEmbedding.document_id == Document.id)
                .where(ExplanationEmbedding.model_id == model_id)
                .order_by(distance)
                .limit(limit * 2)
            )
            mode = "vector"
    if mode == "text":
        # pg_trgm supplies a useful fallback for short questions and inflected names.
        fuzzy = func.similarity(Document.plain_text, query)
        statement = (
            base.add_columns(score.label("score"), fuzzy.label("fuzzy"))
            .where((score > 0) | (fuzzy > 0.04))
            .order_by((score + fuzzy).desc())
            .limit(limit * 2)
        )
    rows = (await session.execute(statement)).all()
    results = []
    for row in rows:
        document, quest = cast(Document, row[0]), cast(Quest, row[1])
        ordinal_or_score, score_or_fuzzy = cast(float, row[2]), cast(float, row[3])
        payload = await public_document(session, document, quest, release, language)
        if payload is None:
            continue
        if mode == "vector":
            blocks = [document.body_ast[int(ordinal_or_score)]]
            rank = float(score_or_fuzzy)
        else:
            words = query.casefold().split()
            blocks = sorted(
                document.body_ast,
                key=lambda block: sum(word in block["text"].casefold() for word in words),
                reverse=True,
            )[:2]
            rank = float(ordinal_or_score) + float(score_or_fuzzy)
        results.append({**payload, "blocks": blocks, "score": rank})
        if len(results) >= limit:
            break
    return {
        "results": results,
        "mode": mode,
        "game_version": release.game_version,
        "locale": language.code,
    }
