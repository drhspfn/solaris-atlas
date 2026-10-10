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

@mcp.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_dialogue",
            description="Search millions of actual in-game spoken dialogue lines and subtitles by text, character name, or quest ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Dialogue text or phrase to search for in spoken lines."
                    },
                    "character": {
                        "type": "string",
                        "description": "Optional character/speaker canonical key (e.g. 'rover', 'yangyang', 'jiyan')."
                    },
                    "quest_id": {
                        "type": "integer",
                        "description": "Optional numeric game quest ID to search within."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of dialogue lines to return (default 30, max 100).",
                        "default": 30
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="search_entities",
            description="Search the game database for characters, quests, items, locations, factions, and terms.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term, e.g. 'Jiyan', 'Casket', 'Grand Library', 'Black Shores'."
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional entity category: 'character', 'quest', 'item', 'location', 'area', etc."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (default 20).",
                        "default": 20
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_quest_transcript",
            description="Get the full chronological dialogue transcript and script of a quest.",
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
            name="get_quest",
            description="Get full overview and metadata of a specific quest (title, description, chapter, scenes).",
            inputSchema={
                "type": "object",
                "properties": {
                    "quest_id": {
                        "type": "string",
                        "description": "Game quest ID (e.g. '101001') or quest canonical key."
                    }
                },
                "required": ["quest_id"]
            }
        ),
        Tool(
            name="get_node_info",
            description="Inspect a specific entity in the knowledge graph by its canonical_key, including its connections/relations to other entities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "canonical_key": {
                        "type": "string",
                        "description": "Canonical key of the node, e.g. 'character_jiyan', 'quest_101001', 'item_xxx'."
                    }
                },
                "required": ["canonical_key"]
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

@mcp.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    if not arguments:
        arguments = {}

    from sqlalchemy import select
    from wuwa_story.db.session import SessionFactory
    from wuwa_story.db.models.graph import Node, NodeType
    from wuwa_story.db.models.core import Quest
    from wuwa_story.db.models.i18n import Locale
    from wuwa_story.db.models.ontology import RelationType
    from wuwa_story.db.repositories.nodes import get_node
    from wuwa_story.db.repositories.edges import get_edges
    from wuwa_story.db.repositories.search import lexical_search
    from wuwa_story.api.routes.story.shared import _node_label, _quest_info
    from wuwa_story.api.routes.story.transcripts import search_dialogue, quest_transcript

    async with SessionFactory() as session:
        try:
            if name == "search_dialogue":
                query = arguments.get("query", "").strip()
                if not query:
                    raise ValueError("Missing 'query' argument")
                character = arguments.get("character")
                quest_id = arguments.get("quest_id")
                limit = min(int(arguments.get("limit", 30)), 100)

                res = await search_dialogue(
                    q=query,
                    character=character,
                    quest_id=quest_id,
                    locale="en",
                    limit=limit,
                    offset=0,
                    session=session,
                )
                results = res.get("results", [])
                if not results:
                    return [TextContent(type="text", text=f"No dialogue lines found matching '{query}'.")]

                output_lines = [f"Found {len(results)} dialogue line(s) for '{query}':\n"]
                for i, r in enumerate(results, 1):
                    speaker = r.get("speaker") or "Narrator / Unknown"
                    text = r.get("text") or ""
                    line_id = r.get("id")
                    game_ids = r.get("game_ids") or []
                    qid = r.get("quest_id") or (game_ids[0] if game_ids else "N/A")
                    output_lines.append(f"[{i}] {speaker} (Quest: {qid}, Line ID: {line_id}):\n    \"{text}\"\n")
                return [TextContent(type="text", text="\n".join(output_lines))]

            elif name == "search_entities":
                query = arguments.get("query", "").strip()
                if not query:
                    raise ValueError("Missing 'query' argument")
                category = arguments.get("category")
                limit = min(int(arguments.get("limit", 20)), 100)

                locale_id = await session.scalar(select(Locale.id).where(Locale.code == "en"))
                categories = [category] if category else None
                res = await lexical_search(
                    session,
                    query,
                    limit=limit,
                    categories=categories,
                    locale_id=locale_id,
                )
                if not res:
                    return [TextContent(type="text", text=f"No entities found for '{query}'.")]

                output_lines = [f"Database entities matching '{query}':\n"]
                for i, item in enumerate(res, 1):
                    node_id = item.get("id")
                    label_info = await _node_label(session, node_id, "en") if node_id else {}
                    title = label_info.get("label") or item.get("alias") or item.get("canonical_key")
                    node_type = item.get("node_type") or "entity"
                    key = item.get("canonical_key")
                    output_lines.append(f"[{i}] [{node_type.upper()}] {title} | Canonical Key: {key} (ID: {node_id})")
                return [TextContent(type="text", text="\n".join(output_lines))]

            elif name == "get_quest_transcript":
                quest_id = arguments.get("quest_id")
                if not quest_id:
                    raise ValueError("Missing 'quest_id' argument")
                character = arguments.get("character")
                limit = min(int(arguments.get("limit", 100)), 300)

                res = await quest_transcript(
                    game_quest_id=int(quest_id),
                    character=character,
                    q=None,
                    locale="en",
                    limit=limit,
                    offset=0,
                    session=session,
                )
                quest_meta = res.get("quest", {})
                lines = res.get("lines", [])
                if not lines:
                    return [TextContent(type="text", text=f"No dialogue lines found for quest {quest_id}.")]

                output_lines = [
                    f"Transcript for Quest {quest_id}: {quest_meta.get('title', 'Unknown Title')}",
                    f"Description: {quest_meta.get('description', 'N/A')}\n"
                ]
                for r in lines:
                    speaker = r.get("speaker") or "Narrator"
                    text = r.get("text") or ""
                    output_lines.append(f"{speaker}: \"{text}\"")
                return [TextContent(type="text", text="\n".join(output_lines))]

            elif name == "get_quest":
                quest_id_arg = str(arguments.get("quest_id", "")).strip()
                if not quest_id_arg:
                    raise ValueError("Missing 'quest_id' argument")

                quest = None
                if quest_id_arg.isdigit():
                    quest = await session.scalar(select(Quest).where(Quest.game_quest_id == int(quest_id_arg)))
                if not quest:
                    node = await session.scalar(select(Node).where(Node.canonical_key == quest_id_arg))
                    if node:
                        quest = await session.scalar(select(Quest).where(Quest.node_id == node.id))

                if not quest:
                    return [TextContent(type="text", text=f"Quest '{quest_id_arg}' not found.")]

                info = await _quest_info(session, quest, locale="en")
                output = (
                    f"Quest Details:\n"
                    f"Title: {info.get('title')}\n"
                    f"Game Quest ID: {quest.game_quest_id}\n"
                    f"Category: {info.get('category')}\n"
                    f"Chapter: {info.get('chapter')}\n"
                    f"Description: {info.get('description')}\n"
                )

                from wuwa_story.db.models.lore import LoreChunk
                chunks = list(
                    await session.scalars(
                        select(LoreChunk).where(LoreChunk.quest_id == str(quest.game_quest_id)).order_by(LoreChunk.id)
                    )
                )
                if chunks:
                    output += "\nSynthesized Lore Summaries:\n"
                    for i, c in enumerate(chunks, 1):
                        output += f"[{i}] ({c.chunk_type}): {c.content}\n"

                return [TextContent(type="text", text=output)]

            elif name == "get_node_info":
                canonical_key = arguments.get("canonical_key", "").strip()
                if not canonical_key:
                    raise ValueError("Missing 'canonical_key' argument")

                node = await get_node(session, canonical_key)
                if not node:
                    return [TextContent(type="text", text=f"Node with key '{canonical_key}' not found.")]

                node_type = await session.get(NodeType, node.type_id)
                label_info = await _node_label(session, node.id, "en")
                title = label_info.get("label") or node.canonical_key

                edges = await get_edges(session, node.id, direction="both", limit=50)
                edge_lines = []
                for edge in edges:
                    rel = await session.get(RelationType, edge.relation_type_id)
                    rel_name = rel.key if rel else f"rel_{edge.relation_type_id}"
                    is_outgoing = edge.from_node_id == node.id
                    other_id = edge.to_node_id if is_outgoing else edge.from_node_id
                    direction_symbol = "-->" if is_outgoing else "<--"
                    other_node = await session.get(Node, other_id)
                    other_key = other_node.canonical_key if other_node else f"id:{other_id}"
                    edge_lines.append(f"  {direction_symbol} [{rel_name}] {other_key}")

                output = (
                    f"Node: {title}\n"
                    f"Canonical Key: {node.canonical_key}\n"
                    f"Type: {node_type.key if node_type else 'unknown'}\n"
                    f"Slug: {node.slug}\n"
                )
                if edge_lines:
                    output += "\nGraph Connections:\n" + "\n".join(edge_lines)
                else:
                    output += "\nNo direct graph relations found."
                return [TextContent(type="text", text=output)]

            elif name == "search_lore":
                query = arguments.get("query", "").strip()
                if not query:
                    raise ValueError("Missing 'query' argument")
                limit = int(arguments.get("limit", 10))

                query_embedding = await generate_query_embedding(session, query)
                service = LoreSearchService(session)
                results = await service.hybrid_search(query, query_embedding, limit=limit)

                if not results:
                    return [TextContent(type="text", text=f"No lore information found for '{query}'.")]

                output = ""
                for i, r in enumerate(results, 1):
                    output += f"[{i}] Chunk Type: {r.chunk_type} (Quest: {r.quest_id or 'N/A'})\n"
                    output += f"{r.content}\n\n"
                return [TextContent(type="text", text=output)]

            elif name == "get_character_timeline":
                character = arguments.get("character", "").strip()
                if not character:
                    raise ValueError("Missing 'character' argument")

                from wuwa_story.db.models.lore import LoreChunk
                stmt = select(LoreChunk).where(LoreChunk.characters.contains([character])).order_by(LoreChunk.id)
                chunks = list(await session.scalars(stmt))

                if not chunks:
                    return [TextContent(type="text", text=f"No timeline events found for character '{character}'.")]

                output = f"Timeline for {character}:\n\n"
                for i, r in enumerate(chunks, 1):
                    output += f"[{i}] Quest: {r.quest_id} | Type: {r.chunk_type}\n{r.content}\n\n"
                return [TextContent(type="text", text=output)]

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
