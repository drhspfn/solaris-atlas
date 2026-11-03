"""Read-only public archive tools; privileged work uses a different registry."""

from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.providers import ToolCall
from wuwa_story.api import read_projections


def read_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "search_dialogue",
            "description": "Search actual spoken in-game dialogue lines and subtitles across all quests, scenes, and talk items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Spoken text or words to search for in dialogue",
                    },
                    "character": {
                        "type": "string",
                        "description": "Optional character name or speaker",
                    },
                    "quest_id": {"type": "integer", "description": "Optional numeric quest ID"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_entities",
            "description": "Search the database for characters, quests, items, and locations by name or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Entity name or keyword"},
                    "category": {
                        "type": "string",
                        "description": "Optional category: character, quest, item, location",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_lore",
            "description": "Search synthesized storyline explanations, cutscenes, and lore notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    ]


async def execute_read(session: AsyncSession, call: ToolCall) -> dict[str, Any]:
    if call.name == "get_quest_lore":
        from wuwa_story.api.routes.story.transcripts import quest_transcript

        result_data = await quest_transcript(
            str(call.arguments["quest_id"]),
            character=None,
            q=None,
            locale="en",
            game_version=None,
            limit=100,
            offset=0,
            focus_line=None,
            include_raw=False,
            session=session,
        )
    elif call.name == "search_dialogue":
        query = call.arguments.get("query", "")
        character = call.arguments.get("character")
        quest_id = call.arguments.get("quest_id")
        from wuwa_story.api.routes.story.transcripts import (
            search_dialogue as api_search_dialogue,
        )

        try:
            res = await api_search_dialogue(
                q=query,
                character=character,
                quest_id=quest_id,
                locale="en",
                limit=15,
                offset=0,
                session=session,
            )
            lines = []
            for r in res.get("results", []):
                speaker = r.get("speaker") or "Narrator"
                text = r.get("text") or ""
                q_id = r.get("quest_id") or (r.get("game_ids") or ["N/A"])[0]
                lines.append(f'[{speaker} in Quest {q_id}]: "{text}"')
            result_data = {"results": "\n".join(lines) if lines else "No dialogue lines found"}
        except Exception as e:
            if cast(Any, read_projections).should_route_to_projection(
                ("dialogue", "character") if character else "dialogue"
            ) and isinstance(e, HTTPException):
                raise
            result_data = {"error": type(e).__name__}

    elif call.name == "search_entities":
        query = call.arguments.get("query", "")
        category = call.arguments.get("category")
        from sqlalchemy import select

        from wuwa_story.api.routes.story.shared import _node_label
        from wuwa_story.db.models.i18n import Locale
        from wuwa_story.db.repositories.search import lexical_search

        projected_search = cast(Any, read_projections).should_route_search_to_projection(
            [category] if category else None
        )
        try:
            if projected_search:
                res = (
                    await cast(Any, read_projections).search_page(
                        session,
                        query,
                        category=[category] if category else None,
                        limit=10,
                    )
                )["results"]
            else:
                locale_id = await session.scalar(select(Locale.id).where(Locale.code == "en"))
                res = await lexical_search(
                    session,
                    query,
                    limit=10,
                    categories=[category] if category else None,
                    locale_id=locale_id,
                )
            items = []
            for r in res:
                label = (
                    r["title"]
                    if projected_search
                    else (await cast(Any, _node_label)(session, r["id"], "en")).get("label")
                    or r.get("alias")
                )
                items.append(f"[{r.get('node_type')}] {label} (key: {r.get('canonical_key')})")
            result_data = {"results": "\n".join(items) if items else "No entities found"}
        except Exception as e:
            if projected_search and isinstance(e, HTTPException):
                raise
            result_data = {"error": type(e).__name__}

    elif call.name == "search_lore":
        query = call.arguments.get("query", "")
        from wuwa_story.search.embedding import generate_query_embedding
        from wuwa_story.search.lore import LoreSearchService

        try:
            query_embedding = await generate_query_embedding(session, query)
            service = LoreSearchService(session)
            results = await service.hybrid_search(query, query_embedding, limit=5)
            output = ""
            for i, r in enumerate(results, 1):
                output += f"[{i}] Quest: {r.quest_id} | Type: {r.chunk_type}\n{r.content}\n\n"
            result_data = {"results": output if output else "No results found"}
        except Exception as e:
            result_data = {"error": type(e).__name__}
    else:
        result_data = {"error": "Unknown tool"}

    return dict(result_data)
