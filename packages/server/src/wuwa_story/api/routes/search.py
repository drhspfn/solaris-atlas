from ipaddress import ip_address
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api import read_projections
from wuwa_story.api.dependencies import get_cache
from wuwa_story.auth.dependencies import AuthContext, optional_auth_context
from wuwa_story.cache import CacheManager
from wuwa_story.db.models.graph import NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.repositories.character_identity import annotate_character_cards
from wuwa_story.db.repositories.search import lexical_search
from wuwa_story.db.session import get_session
from wuwa_story.read_models.context import source_build
from wuwa_story.storage.entity_media import entity_image_urls

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def search(
    q: str = Query(min_length=1, max_length=512),
    category: list[str] | None = Query(default=None),
    scope: str = Query("entities", pattern="^(entities|dialogue)$"),
    locale: str = "en",
    game_version: str | None = None,
    locale_id: int | None = None,
    sort_by: str = Query("relevance", pattern="^(relevance|name)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    character: str | None = None,
    character_kind: Literal["playable", "story", "npc", "technical", "all"] = "all",
    quest_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if hasattr(sort_by, "default"):
        sort_by = sort_by.default
    if hasattr(sort_order, "default"):
        sort_order = sort_order.default
    if hasattr(scope, "default"):
        scope = scope.default
    if hasattr(limit, "default"):
        limit = limit.default
    if hasattr(offset, "default"):
        offset = offset.default
    if hasattr(q, "default"):
        q = q.default

    if category and "dialogue" in category:
        if len(category) > 1 and set(category) != {"dialogue"}:
            raise HTTPException(
                status_code=422,
                detail="Search one scope at a time; use scope=dialogue or entity categories.",
            )
        scope = "dialogue"
    if read_projections.should_route_search_to_projection(category, scope, game_version, character, locale=locale, locale_id=locale_id):
        return await read_projections.search_page(
            session, q, category, scope, locale, game_version, locale_id, sort_by, sort_order, limit, offset, character, quest_id, character_kind=character_kind,
        )
    active_release = await session.scalar(
        select(GameRelease).order_by(GameRelease.sequence.desc()).limit(1)
    )
    if (
        scope == "entities"
        and game_version
        and active_release
        and game_version != active_release.game_version
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Search index is built for {active_release.game_version}, not {game_version}.",
        )
    if scope == "dialogue":
        if sort_by != "relevance":
            raise HTTPException(status_code=422, detail="Dialogue search uses authored order.")
        # Reuse the source-aware transcript search; keep its full response available at
        # /dialogue/search and return a compact, list-card shape from this unified route.
        from wuwa_story.api.routes.story import search_dialogue

        # Preserve the source routing decision across the delegated endpoint.
        with source_build():
            dialogue_page = await search_dialogue(
                q=q,
                character=character,
                quest_id=quest_id,
                locale=locale,
                game_version=game_version,
                limit=limit,
                offset=offset,
                session=session,
            )
        compact = []
        for line in dialogue_page["results"]:
            text = line["text"]
            compact.append(
                {
                    "id": line["id"],
                    "canonical_key": line["id"],
                    "node_type": "dialogue_line",
                    "category": "dialogue",
                    "speaker": line["speaker"],
                    "text": text,
                    "game_ids": line["game_ids"],
                    "source_type": line["source_type"],
                    "source_index": line["source_index"],
                    "flow_state": line["flow_state"],
                    "action": {
                        "id": line["action"]["id"],
                        "name": line["action"]["name"],
                        "index": line["action"]["index"],
                    }
                    if line["action"]
                    else None,
                    "source": {
                        "file": line["provenance"]["source_file"],
                        "row": line["provenance"]["source_row"],
                    }
                    if line["provenance"]
                    else None,
                }
            )
        return {
            "query": q,
            "scope": "dialogue",
            "snapshot_version": game_version or (active_release.game_version if active_release else None),
            "locale": locale,
            "filters": {"character": character, "quest_id": quest_id},
            "sort": {"by": "authored_order", "order": "asc"},
            "limit": limit,
            "offset": offset,
            "has_more": len(compact) == limit,
            "results": compact,
        }
    aliases = {"location": ["area", "location"], "dialogue": ["dialogue_line", "talk_item"]}
    categories = list(
        dict.fromkeys(
            node_type
            for value in category or []
            for node_type in aliases.get(value, [value])
        )
    )
    if categories:
        known = set(await session.scalars(select(NodeType.key)))
        unknown = set(categories) - known
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown categories: {sorted(unknown)}")
    if locale_id is None:
        locale_id = await session.scalar(select(Locale.id).where(Locale.code == locale))
        if locale_id is None:
            raise HTTPException(status_code=400, detail=f"Unknown locale: {locale}")
    results = await lexical_search(
        session,
        q,
        limit=limit,
        categories=categories,
        character_kind=character_kind,
        locale_id=locale_id,
        sort_by=sort_by,
        sort_order=sort_order,
        offset=offset,
    )
    # A result may have matched an item's description instead of its display
    # name. Keep the matched alias for traceability, but always give catalog
    # cards a canonical localized title.
    from wuwa_story.api.routes.story import _node_label
    from wuwa_story.story.service import prefetch_node_labels

    images = await entity_image_urls(session, [result["id"] for result in results])
    await prefetch_node_labels(session, [result["id"] for result in results], locale)
    for result in results:
        result["image_url"] = images.get(result["id"])
        label = await _node_label(session, result["id"], locale)
        result["title"] = label["label"]
        result.update({key: label[key] for key in ("slug", "href")})
        result["category"] = (
            "location"
            if result["node_type"] in {"area", "location"}
            else "dialogue"
            if result["node_type"] in {"dialogue_line", "talk_item"}
            else result["node_type"]
        )
    await annotate_character_cards(session, results)
    return {
        "query": q,
        "scope": "entities",
        "mode": "lexical",
        "semantic_available": False,
        "snapshot_version": active_release.game_version if active_release else None,
        "filters": {"categories": category or [], "locale": locale},
        "sort": {"by": sort_by, "order": sort_order},
        "limit": limit,
        "offset": offset,
        "has_more": len(results) == limit,
        "results": results,
    }

@router.get("/lore")
async def search_lore(
    q: str = Query(min_length=1, max_length=512),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from wuwa_story.search.embedding import generate_query_embedding
    from wuwa_story.search.lore import LoreSearchService

    try:
        query_embedding = await generate_query_embedding(session, q)
    except Exception:
        query_embedding = None

    service = LoreSearchService(session)
    results = await service.hybrid_search(q, query_embedding, limit=limit)

    return {
        "query": q,
        "scope": "lore",
        "mode": "hybrid" if query_embedding else "lexical",
        "limit": limit,
        "results": [
            {
                "chunk_id": r.chunk_id,
                "source_type": r.source_type,
                "source_id": r.source_id,
                "quest_id": r.quest_id,
                "chunk_type": r.chunk_type,
                "content": r.content,
                "score": r.score,
            }
            for r in results
        ],
    }

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)

class PublicChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)

@router.post("/chat")
async def public_chat(
    request: Request,
    body: PublicChatRequest,
    session: AsyncSession = Depends(get_session),
    cache: CacheManager = Depends(get_cache),
    context: AuthContext | None = Depends(optional_auth_context),
) -> dict[str, Any]:
    try:
        ip = ip_address(request.client.host) if request.client else None
    except ValueError:
        ip = None
    if ip is None:
        raise HTTPException(503, "Client address is unavailable", headers={"Retry-After": "2"})
    # Canonicalize IPv4-mapped IPv6 so the same visitor cannot get two quotas.
    actor = str(getattr(ip, "ipv4_mapped", None) or ip)
    keys = ["public-chat:" + actor]
    if context is not None:
        keys.append(f"public-chat:user:{context.user.id}")
    quota = await cache.rate_limits(keys, limit=5, window_seconds=86400)
    if not quota.allowed:
        raise HTTPException(429, "Daily rate limit exceeded (5 requests per account or IP per day).",
                            headers={"Retry-After": str(quota.retry_after)})

    from wuwa_story.agents.chat import run_chat
    return await run_chat(session, body.messages, role="public")
