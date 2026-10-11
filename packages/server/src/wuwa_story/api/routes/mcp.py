import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool

from wuwa_story.search.embedding import generate_query_embedding
from wuwa_story.search.lore import LoreSearchService

logger = logging.getLogger(__name__)

mcp = Server("solaris-atlas-lore")
streamable_http = StreamableHTTPSessionManager(app=mcp, json_response=True)


def _dump_json(obj: Any) -> str:
    def default(o: Any) -> Any:
        if hasattr(o, "model_dump"):
            return o.model_dump()
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)
    return json.dumps(obj, indent=2, ensure_ascii=False, default=default)


@mcp.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="search",
            description=(
                "Direct replica of the Solaris Atlas public GET /api/search endpoint. "
                "Searches the entire game archive. By default searches 'entities' (characters, quests, items, locations, areas), "
                "or with scope='dialogue' searches millions of actual spoken in-game dialogue lines and subtitles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Search query text (e.g. 'rover', 'jiyan', 'sword', 'grand library')."
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["entities", "dialogue"],
                        "description": "Search scope: 'entities' for characters/items/quests/locations, or 'dialogue' for spoken lines. Default is 'entities'.",
                        "default": "entities"
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter (e.g. 'character', 'quest', 'item', 'location', 'area')."
                    },
                    "locale": {
                        "type": "string",
                        "description": "Language code for localized text (e.g. 'en', 'ru', 'zh-Hans', 'ja'). Default is 'en'.",
                        "default": "en"
                    },
                    "character": {
                        "type": "string",
                        "description": "Filter by speaker/character when searching dialogue."
                    },
                    "quest_id": {
                        "type": "integer",
                        "description": "Filter by game quest ID when searching dialogue."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 20, max 100).",
                        "default": 20
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Result offset for pagination (default 0).",
                        "default": 0
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["relevance", "name"],
                        "description": "Sort results by 'relevance' or 'name'. Default 'relevance'.",
                        "default": "relevance"
                    },
                    "sort_order": {
                        "type": "string",
                        "enum": ["desc", "asc"],
                        "description": "Sort order: 'desc' or 'asc'. Default 'desc'.",
                        "default": "desc"
                    }
                },
                "required": ["q"]
            }
        ),
        Tool(
            name="get_node",
            description="Get detailed attributes of any knowledge graph node by its ID (integer) or canonical_key (string).",
            inputSchema={
                "type": "object",
                "properties": {
                    "id_or_key": {
                        "type": ["string", "integer"],
                        "description": "Numeric node ID (e.g. 105) or canonical_key (e.g. 'character_rover', 'quest_101001')."
                    },
                    "locale": {
                        "type": "string",
                        "description": "Locale for localized title/name (default 'en').",
                        "default": "en"
                    }
                },
                "required": ["id_or_key"]
            }
        ),
        Tool(
            name="get_node_related",
            description=(
                "Graph transition tool (переход по нодам). Replica of GET /api/nodes/{canonical_key}/related. "
                "Traverses relations to neighboring nodes in the story graph (e.g. quests where a character appears, "
                "items associated with a person, locations connected to a quest)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id_or_key": {
                        "type": ["string", "integer"],
                        "description": "Node ID or canonical_key to start from."
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["both", "out", "in"],
                        "description": "Relation direction: 'out' (outgoing), 'in' (incoming), or 'both'. Default 'both'.",
                        "default": "both"
                    },
                    "relation": {
                        "type": "string",
                        "description": "Filter by relation type key (e.g. 'appears_in', 'has_plot_step', 'presents_scene', 'located_in')."
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter neighbor nodes by category (e.g. 'character', 'quest', 'location', 'item')."
                    },
                    "locale": {
                        "type": "string",
                        "description": "Locale for neighbor entity labels (default 'en').",
                        "default": "en"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max linked neighbors to return (default 50).",
                        "default": 50
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Offset for pagination (default 0).",
                        "default": 0
                    }
                },
                "required": ["id_or_key"]
            }
        ),
        Tool(
            name="get_node_edges",
            description="Get raw graph edges connected to a node. Replica of GET /api/nodes/{canonical_key}/edges.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id_or_key": {
                        "type": ["string", "integer"],
                        "description": "Node ID or canonical_key."
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["both", "out", "in"],
                        "description": "Direction of edges ('in', 'out', 'both'). Default 'both'.",
                        "default": "both"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max edges to return (default 100).",
                        "default": 100
                    }
                },
                "required": ["id_or_key"]
            }
        ),
        Tool(
            name="get_node_narrative_context",
            description=(
                "Resolve narrative context for a dialogue node (surrounding spoken lines and associated quests). "
                "Replica of GET /api/nodes/{canonical_key}/narrative-context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id_or_key": {
                        "type": ["string", "integer"],
                        "description": "Dialogue line node ID or canonical_key."
                    },
                    "locale": {
                        "type": "string",
                        "description": "Locale code (default 'en').",
                        "default": "en"
                    }
                },
                "required": ["id_or_key"]
            }
        ),
        Tool(
            name="get_quest_transcript",
            description="Get the full chronological dialogue transcript and script of a quest. Replica of GET /api/quests/{id}/transcript.",
            inputSchema={
                "type": "object",
                "properties": {
                    "quest_id": {
                        "type": "integer",
                        "description": "Numeric game quest ID (e.g. 101001)."
                    },
                    "character": {
                        "type": "string",
                        "description": "Optional character to filter dialogue for."
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional keyword filter for lines."
                    },
                    "locale": {
                        "type": "string",
                        "description": "Language locale (default 'en').",
                        "default": "en"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to return (default 100).",
                        "default": 100
                    }
                },
                "required": ["quest_id"]
            }
        ),
        Tool(
            name="search_lore",
            description="Search synthesized storyline explanations, cutscene analyses, and lore notes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query or lore question."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results to return (default 10).",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_character_timeline",
            description="Chronology of events and storyline appearances for a specific character.",
            inputSchema={
                "type": "object",
                "properties": {
                    "character": {"type": "string"}
                },
                "required": ["character"]
            }
        )
    ]


async def _resolve_node(session: Any, id_or_key: str | int) -> Any:
    from wuwa_story.db.models.graph import Node
    from wuwa_story.db.repositories.nodes import get_node

    val = str(id_or_key).strip()
    if val.isdigit():
        node = await session.get(Node, int(val))
        if node:
            return node
    return await get_node(session, val)


@mcp.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    if not arguments:
        arguments = {}

    from sqlalchemy import select
    from wuwa_story.db.session import SessionFactory
    from wuwa_story.db.models.graph import Node, NodeType
    from wuwa_story.api.routes.search import search as api_search
    from wuwa_story.api.routes.nodes import node_related, node_edges, node_narrative_context
    from wuwa_story.api.routes.story.transcripts import quest_transcript
    from wuwa_story.api.routes.story.shared import _node_label

    async with SessionFactory() as session:
        try:
            if name in ["search", "search_entities", "search_dialogue"]:
                q_val = str(arguments.get("q") or arguments.get("query") or "").strip()
                if not q_val:
                    raise ValueError("Missing 'q' or 'query' argument")

                default_scope = "dialogue" if name == "search_dialogue" else "entities"
                scope_val = arguments.get("scope", default_scope)

                category_arg = arguments.get("category")
                if isinstance(category_arg, str):
                    category_list = [category_arg]
                elif isinstance(category_arg, list):
                    category_list = category_arg
                else:
                    category_list = None

                sort_by_val = str(arguments.get("sort_by") or "relevance").strip()
                if sort_by_val not in {"relevance", "name"}:
                    sort_by_val = "relevance"
                sort_order_val = str(arguments.get("sort_order") or "desc").strip()
                if sort_order_val not in {"asc", "desc"}:
                    sort_order_val = "desc"

                res = await api_search(
                    q=q_val,
                    category=category_list,
                    scope=scope_val,
                    locale=arguments.get("locale", "en"),
                    sort_by=sort_by_val,
                    sort_order=sort_order_val,
                    limit=min(int(arguments.get("limit", 20)), 100),
                    offset=int(arguments.get("offset", 0)),
                    character=arguments.get("character"),
                    quest_id=arguments.get("quest_id"),
                    session=session,
                )
                return [TextContent(type="text", text=_dump_json(res))]

            elif name == "get_node":
                id_or_key = arguments.get("id_or_key")
                if not id_or_key:
                    raise ValueError("Missing 'id_or_key' argument")

                node = await _resolve_node(session, id_or_key)
                if not node:
                    return [TextContent(type="text", text=f"Node '{id_or_key}' not found.")]

                node_type = await session.get(NodeType, node.type_id)
                locale = arguments.get("locale", "en")
                label_info = await _node_label(session, node.id, locale)
                data = {
                    "id": node.id,
                    "canonical_key": node.canonical_key,
                    "slug": node.slug,
                    "type": node_type.key if node_type else None,
                    "title": label_info.get("label"),
                    "metadata": node.metadata_json,
                }
                return [TextContent(type="text", text=_dump_json(data))]

            elif name == "get_node_related":
                id_or_key = arguments.get("id_or_key")
                if not id_or_key:
                    raise ValueError("Missing 'id_or_key' argument")

                node = await _resolve_node(session, id_or_key)
                if not node:
                    return [TextContent(type="text", text=f"Node '{id_or_key}' not found.")]

                res = await node_related(
                    canonical_key=node.canonical_key,
                    direction=arguments.get("direction", "both"),
                    relation=arguments.get("relation"),
                    category=arguments.get("category"),
                    locale=arguments.get("locale", "en"),
                    limit=min(int(arguments.get("limit", 50)), 200),
                    offset=int(arguments.get("offset", 0)),
                    session=session,
                )
                return [TextContent(type="text", text=_dump_json(res))]

            elif name == "get_node_edges":
                id_or_key = arguments.get("id_or_key")
                if not id_or_key:
                    raise ValueError("Missing 'id_or_key' argument")

                node = await _resolve_node(session, id_or_key)
                if not node:
                    return [TextContent(type="text", text=f"Node '{id_or_key}' not found.")]

                edges = await node_edges(
                    canonical_key=node.canonical_key,
                    direction=arguments.get("direction", "both"),
                    limit=min(int(arguments.get("limit", 100)), 500),
                    session=session,
                )
                return [TextContent(type="text", text=_dump_json([e.model_dump() for e in edges]))]

            elif name == "get_node_narrative_context":
                id_or_key = arguments.get("id_or_key")
                if not id_or_key:
                    raise ValueError("Missing 'id_or_key' argument")

                node = await _resolve_node(session, id_or_key)
                if not node:
                    return [TextContent(type="text", text=f"Node '{id_or_key}' not found.")]

                res = await node_narrative_context(
                    canonical_key=node.canonical_key,
                    locale=arguments.get("locale", "en"),
                    session=session,
                )
                return [TextContent(type="text", text=_dump_json(res))]

            elif name in ["get_quest_transcript", "get_quest"]:
                quest_id = arguments.get("quest_id") or arguments.get("id_or_key")
                if not quest_id:
                    raise ValueError("Missing 'quest_id' argument")

                # If passed as canonical key, resolve to numeric game_quest_id
                if not str(quest_id).isdigit():
                    node = await _resolve_node(session, quest_id)
                    from wuwa_story.db.models.core import Quest
                    quest_row = await session.scalar(select(Quest).where(Quest.node_id == node.id)) if node else None
                    if quest_row:
                        quest_id = quest_row.game_quest_id
                    else:
                        return [TextContent(type="text", text=f"Quest '{quest_id}' not found.")]

                res = await quest_transcript(
                    game_quest_id=int(quest_id),
                    character=arguments.get("character"),
                    q=arguments.get("query"),
                    locale=arguments.get("locale", "en"),
                    limit=min(int(arguments.get("limit", 100)), 500),
                    offset=int(arguments.get("offset", 0)),
                    session=session,
                )
                return [TextContent(type="text", text=_dump_json(res))]

            elif name == "search_lore":
                query = str(arguments.get("query", arguments.get("q", ""))).strip()
                if not query:
                    raise ValueError("Missing 'query' argument")
                limit = int(arguments.get("limit", 10))

                query_embedding = await generate_query_embedding(session, query)
                service = LoreSearchService(session)
                results = await service.hybrid_search(query, query_embedding, limit=limit)

                if not results:
                    return [TextContent(type="text", text=f"No lore information found for '{query}'.")]

                data = [
                    {
                        "chunk_id": r.chunk_id,
                        "chunk_type": r.chunk_type,
                        "quest_id": r.quest_id,
                        "content": r.content,
                        "score": r.score,
                    }
                    for r in results
                ]
                return [TextContent(type="text", text=_dump_json(data))]

            elif name == "get_character_timeline":
                character = str(arguments.get("character", "")).strip()
                if not character:
                    raise ValueError("Missing 'character' argument")

                from wuwa_story.db.models.lore import LoreChunk
                stmt = select(LoreChunk).where(LoreChunk.characters.contains([character])).order_by(LoreChunk.id)
                chunks = list(await session.scalars(stmt))

                if not chunks:
                    return [TextContent(type="text", text=f"No timeline events found for character '{character}'.")]

                data = [
                    {
                        "id": c.id,
                        "quest_id": c.quest_id,
                        "chunk_type": c.chunk_type,
                        "content": c.content,
                    }
                    for c in chunks
                ]
                return [TextContent(type="text", text=_dump_json(data))]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as e:
            logger.exception("Error executing tool %s", name)
            return [TextContent(type="text", text=f"Tool execution failed: {e}")]


router = APIRouter(prefix="/mcp", tags=["mcp"])

sse_transport: SseServerTransport | None = None

@router.get("/sse")
async def sse(request: Request):
    """
    Client connects to this endpoint to receive Server-Sent Events from the MCP server.
    """
    global sse_transport
    from sse_starlette.sse import EventSourceResponse

    sse_transport = SseServerTransport("/mcp/messages")
    
    async def run_server():
        # Initialization options
        from mcp.server import InitializationOptions
        options = InitializationOptions(
            server_name="solaris-atlas-lore",
            server_version="0.1.0",
            capabilities=mcp.get_capabilities()
        )
        await mcp.run(sse_transport, options)

    import asyncio
    asyncio.create_task(run_server())

    return EventSourceResponse(sse_transport.handle_sse(request))

@router.post("/messages")
async def messages(request: Request):
    """
    Client sends JSON-RPC messages to this endpoint via POST.
    """
    global sse_transport
    if sse_transport is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="SSE connection not established")
        
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=202, content={"status": "accepted"})
