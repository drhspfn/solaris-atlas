import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, Request
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.session import get_session
from wuwa_story.search.lore import LoreSearchService
from wuwa_story.search.embedding import generate_query_embedding

logger = logging.getLogger(__name__)

mcp = Server("solaris-atlas-lore")

@mcp.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_lore",
            description="Semantic search across the Wuthering Waves storyline (cutscenes, quests, characters, mysteries).",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query, e.g. 'What happened to Denia on the frozen lake?'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results to return (default 10)",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_quest",
            description="Get full description of a specific quest.",
            inputSchema={
                "type": "object",
                "properties": {
                    "quest_id": {"type": "string"}
                },
                "required": ["quest_id"]
            }
        ),
        Tool(
            name="get_character_timeline",
            description="Chronology of events for a specific character.",
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
    if name not in ["search_lore", "get_quest", "get_character_timeline"]:
        raise ValueError(f"Unknown tool: {name}")

    if not arguments:
        arguments = {}

    from wuwa_story.db.session import SessionLocal
    
    async with SessionLocal() as session:
        try:
            if name == "search_lore":
                query = arguments.get("query")
                if not query:
                    raise ValueError("Missing 'query' argument")
                limit = arguments.get("limit", 10)
                query_embedding = await generate_query_embedding(session, query)
                service = LoreSearchService(session)
                results = await service.hybrid_search(query, query_embedding, limit=limit)
                
                output = ""
                for i, r in enumerate(results, 1):
                    output += f"[{i}] Chunk Type: {r.chunk_type} (Source: {r.source_id})\n"
                    output += f"{r.content}\n\n"
                    
                if not output:
                    output = "No lore information found for the given query."
                return [TextContent(type="text", text=output)]
                
            elif name == "get_quest":
                quest_id = arguments.get("quest_id")
                from sqlalchemy import select
                from wuwa_story.db.models.lore import LoreChunk
                
                stmt = select(LoreChunk).where(LoreChunk.quest_id == quest_id).order_by(LoreChunk.id)
                result = await session.execute(stmt)
                chunks = result.scalars().all()
                
                if not chunks:
                    return [TextContent(type="text", text=f"No lore chunks found for quest {quest_id}.")]
                
                output = f"Lore for Quest {quest_id}:\n\n"
                for i, r in enumerate(chunks, 1):
                    output += f"[{i}] Type: {r.chunk_type}\n"
                    output += f"{r.content}\n\n"
                    
                return [TextContent(type="text", text=output)]
                
            elif name == "get_character_timeline":
                character = arguments.get("character")
                from sqlalchemy import select
                from wuwa_story.db.models.lore import LoreChunk
                
                # Query chunks where characters JSONB array contains the given character
                stmt = select(LoreChunk).where(LoreChunk.characters.contains([character])).order_by(LoreChunk.id)
                result = await session.execute(stmt)
                chunks = result.scalars().all()
                
                if not chunks:
                    return [TextContent(type="text", text=f"No timeline events found for character {character}.")]
                
                output = f"Timeline for {character}:\n\n"
                for i, r in enumerate(chunks, 1):
                    output += f"[{i}] Quest: {r.quest_id} | Type: {r.chunk_type}\n"
                    output += f"{r.content}\n\n"
                    
                return [TextContent(type="text", text=output)]

        except Exception as e:
            logger.exception("Error executing tool")
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
