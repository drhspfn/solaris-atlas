from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.repositories.search import lexical_search
from wuwa_story.db.session import get_session
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
        locale_id=locale_id,
        sort_by=sort_by,
        sort_order=sort_order,
        offset=offset,
    )
    # A result may have matched an item's description instead of its display
    # name. Keep the matched alias for traceability, but always give catalog
    # cards a canonical localized title.
    from wuwa_story.api.routes.story import _node_label

    images = await entity_image_urls(session, [result["id"] for result in results])
    for result in results:
        result["image_url"] = images.get(result["id"])
        result["title"] = (await _node_label(session, result["id"], locale))["label"]
        result["category"] = (
            "location"
            if result["node_type"] in {"area", "location"}
            else "dialogue"
            if result["node_type"] in {"dialogue_line", "talk_item"}
            else result["node_type"]
        )
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
    from wuwa_story.search.lore import LoreSearchService
    from wuwa_story.search.embedding import generate_query_embedding

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

from pydantic import BaseModel
from datetime import datetime, date
from fastapi import Request

class ChatMessage(BaseModel):
    role: str
    content: str

class PublicChatRequest(BaseModel):
    messages: list[ChatMessage]

ip_rate_limits: dict[str, tuple[date, int]] = {}

@router.post("/chat")
async def public_chat(
    request: Request,
    body: PublicChatRequest,
    session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    ip = request.client.host if request.client else "unknown"
    today = datetime.utcnow().date()
    
    if ip != "unknown":
        last_date, count = ip_rate_limits.get(ip, (today, 0))
        if last_date != today:
            count = 0
            
        if count >= 5:
            raise HTTPException(status_code=429, detail="Daily rate limit exceeded (5 requests per day).")
            
        ip_rate_limits[ip] = (today, count + 1)

    from wuwa_story.agents.providers import Provider
    from wuwa_story.agents.settings import get_agent_settings
    import httpx

    settings = get_agent_settings()
    
    tools = [
        {
            "name": "search_dialogue",
            "description": "Search actual spoken in-game dialogue lines and subtitles across all quests, scenes, and talk items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Spoken text or words to search for in dialogue"},
                    "character": {"type": "string", "description": "Optional character name or speaker"},
                    "quest_id": {"type": "integer", "description": "Optional numeric quest ID"}
                },
                "required": ["query"],
                "additionalProperties": False,
            }
        },
        {
            "name": "search_entities",
            "description": "Search the database for characters, quests, items, and locations by name or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Entity name or keyword"},
                    "category": {"type": "string", "description": "Optional category: character, quest, item, location"}
                },
                "required": ["query"],
                "additionalProperties": False,
            }
        },
        {
            "name": "search_lore",
            "description": "Search synthesized storyline explanations, cutscenes, and lore notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"],
                "additionalProperties": False,
            }
        }
    ]

    history = [
        {
            "role": "system", 
            "content": (
                "You are the Solaris Atlas Assistant, a Wuthering Waves Lore Expert. "
                "You have access to the actual game database: you can search in-game dialogue lines using 'search_dialogue', "
                "search database entities (characters, quests, items) using 'search_entities', and search lore explanations using 'search_lore'. "
                "Answer accurately based on the database. If you don't find the answer, state that it wasn't found in the archives. "
                "Keep your answers concise, accurate, and engaging."
            )
        }
    ]
    
    for msg in body.messages:
        if msg.role in ("user", "assistant"):
            history.append({"role": msg.role, "content": msg.content})

    async with httpx.AsyncClient() as client:
        provider = Provider(settings, client)
        
        for _ in range(5):
            path, payload = provider.request(history, tools)
            raw = await provider.post(path, payload)
            turn = provider.parse(raw)
            
            if turn.calls:
                history.extend(turn.items)
                
                for call in turn.calls:
                    if call.name == "search_dialogue":
                        query = call.arguments.get("query", "")
                        character = call.arguments.get("character")
                        quest_id = call.arguments.get("quest_id")
                        from wuwa_story.api.routes.story.transcripts import search_dialogue as api_search_dialogue
                        try:
                            res = await api_search_dialogue(
                                q=query,
                                character=character,
                                quest_id=quest_id,
                                locale="en",
                                limit=15,
                                offset=0,
                                session=session
                            )
                            lines = []
                            for r in res.get("results", []):
                                speaker = r.get("speaker") or "Narrator"
                                text = r.get("text") or ""
                                q_id = r.get("quest_id") or (r.get("game_ids") or ["N/A"])[0]
                                lines.append(f"[{speaker} in Quest {q_id}]: \"{text}\"")
                            result_data = {"results": "\n".join(lines) if lines else "No dialogue lines found"}
                        except Exception as e:
                            result_data = {"error": str(e)}

                    elif call.name == "search_entities":
                        query = call.arguments.get("query", "")
                        category = call.arguments.get("category")
                        from wuwa_story.db.repositories.search import lexical_search
                        from wuwa_story.api.routes.story.shared import _node_label
                        from sqlalchemy import select
                        from wuwa_story.db.models.i18n import Locale
                        try:
                            locale_id = await session.scalar(select(Locale.id).where(Locale.code == "en"))
                            res = await lexical_search(
                                session,
                                query,
                                limit=10,
                                categories=[category] if category else None,
                                locale_id=locale_id
                            )
                            items = []
                            for r in res:
                                label = (await _node_label(session, r["id"], "en")).get("label") or r.get("alias")
                                items.append(f"[{r.get('node_type')}] {label} (key: {r.get('canonical_key')})")
                            result_data = {"results": "\n".join(items) if items else "No entities found"}
                        except Exception as e:
                            result_data = {"error": str(e)}

                    elif call.name == "search_lore":
                        query = call.arguments.get("query", "")
                        from wuwa_story.search.lore import LoreSearchService
                        from wuwa_story.search.embedding import generate_query_embedding
                        try:
                            query_embedding = await generate_query_embedding(session, query)
                            service = LoreSearchService(session)
                            results = await service.hybrid_search(query, query_embedding, limit=5)
                            output = ""
                            for i, r in enumerate(results, 1):
                                output += f"[{i}] Quest: {r.quest_id} | Type: {r.chunk_type}\n{r.content}\n\n"
                            result_data = {"results": output if output else "No results found"}
                        except Exception as e:
                            result_data = {"error": str(e)}
                    else:
                        result_data = {"error": "Unknown tool"}
                        
                    history.append(provider.tool_result(call, result_data))
            else:
                return {"reply": turn.text}
                
        return {"reply": "I needed too many steps to answer. Please try again."}

