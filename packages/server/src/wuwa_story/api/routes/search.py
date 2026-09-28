from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.graph import NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.repositories.search import lexical_search
from wuwa_story.db.session import get_session

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

    for result in results:
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
