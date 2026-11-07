"""Standalone MCP adapters for public item and cutscene APIs."""

from urllib.parse import quote, urlencode

from mcp.types import Tool

from wuwa_story.api.routes import cutscenes
from wuwa_story.api.routes.story import npc_sales, profiles
from wuwa_story.config.settings import get_settings

_COMMON = {
    "locale": {"type": "string", "default": "en"},
    "game_version": {"type": "string", "description": "Imported game version; omit for the API default."},
}
_PAGING = {
    "limit": {"type": "integer", "minimum": 1},
    "offset": {"type": "integer", "minimum": 0},
}


def _tool(name, description, properties, required=()):
    return Tool(name=name, description=description, inputSchema={
        "type": "object", "properties": {**properties, **_COMMON}, "required": list(required),
    })


TOOLS = [
    _tool("get_item_profile", "Get an item profile, including source-backed acquisition details.",
          {"canonical_key": {"type": "string"}}, ("canonical_key",)),
    _tool("get_item_sellers", "Get authored NPC shop offers for an item. NPC data is available only for the latest imported snapshot.",
          {"item_id": {"type": "integer"}, **_PAGING}, ("item_id",)),
    _tool("search_cutscenes", "Discover cutscenes by title or resource name; omit game_version to search all imported versions.",
          {"q": {"type": "string", "maxLength": 128}, "playable": {"type": "boolean", "default": True},
           **_PAGING, "cursor": {"type": "string", "maxLength": 2048}}),
    _tool("get_cutscene", "Get a cutscene's details, public page, playback media, captions, and available descriptions.",
          {"name": {"type": "string", "description": "Cutscene resource name from search_cutscenes."},
           "visual_offset": {"type": "integer", "minimum": 0, "default": 0},
           "visual_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 12}}, ("name",)),
]


def _page_url(name, locale, version):
    query = {"locale": locale}
    if version:
        query["game_version"] = version
    return f"{get_settings().frontend_url.rstrip('/')}/cutscenes/{quote(name, safe='')}?{urlencode(query)}"


def _pagination(arguments, default, maximum):
    limit, offset = arguments.get("limit", default), arguments.get("offset", 0)
    if type(limit) is not int or not 1 <= limit <= maximum or type(offset) is not int or offset < 0:
        raise ValueError(f"limit must be between 1 and {maximum} and offset must be non-negative")
    return limit, offset


async def _visual_reports(session, media, version, offset=0, limit=12):
    from wuwa_story.agents.cutscene_vision import visual_reference

    reports = []
    for asset_id in dict.fromkeys(item.get("asset_node_id") for item in media.values() if item.get("asset_node_id") is not None):
        report = await visual_reference(session, asset_id, version)
        if report is None:
            continue
        metadata = report.metadata_json or {}
        events = metadata.get("events") or []
        reports.append({"asset_node_id": asset_id, "asset_version": metadata.get("asset_version"),
                        "evidence_type": metadata.get("evidence_type"), "duration": metadata.get("duration"),
                        "events": events[offset:offset + limit], "total_events": len(events),
                        "next_offset": offset + limit if offset + limit < len(events) else None,
                        "limitations": "AI observations of sampled frames, not authored dialogue or proof of earlier-version visuals."})
    return reports


async def dispatch(name, arguments, session):
    """Return the route payload; unknown tool names raise ValueError."""
    arguments = arguments or {}
    locale = arguments.get("locale", "en")
    version = arguments.get("game_version")
    if name == "get_item_profile":
        return await profiles.item_profile(canonical_key=arguments["canonical_key"], locale=locale,
                                           game_version=version, session=session)
    if name == "get_item_sellers":
        limit, offset = _pagination(arguments, 50, 100)
        return await npc_sales.item_npc_sellers(item_id=arguments["item_id"], game_version=version,
                                               locale=locale, limit=limit, offset=offset, session=session)
    if name == "search_cutscenes":
        limit, offset = _pagination(arguments, 24, 48)
        q, cursor = arguments.get("q", ""), arguments.get("cursor")
        if len(q) > 128 or offset > 10000 or (cursor is not None and len(cursor) > 2048):
            raise ValueError("cutscene search parameters exceed API limits")
        result = await cutscenes.catalog(game_version=version, locale=locale, q=q,
                                        playable=arguments.get("playable", True), limit=limit,
                                        offset=offset, cursor=cursor, session=session)
        return {**result, "items": [
            {**item, "public_url": _page_url(item["name"], locale, item.get("game_version"))}
            for item in result["items"]
        ]}
    if name == "get_cutscene":
        result = await cutscenes.detail(name=arguments["name"], game_version=version,
                                        locale=locale, session=session)
        event = result.get("event") or {}
        playback = event.get("playback") or {}
        media = playback.get("media") or {}
        descriptions = [item["description"] for item in media.values() if item.get("description") is not None]
        visual_offset, visual_limit = arguments.get("visual_offset", 0), arguments.get("visual_limit", 12)
        if type(visual_offset) is not int or visual_offset < 0 or type(visual_limit) is not int or not 1 <= visual_limit <= 20:
            raise ValueError("visual_offset must be non-negative and visual_limit must be between 1 and 20")
        reports = await _visual_reports(session, media, playback.get("asset_version"), visual_offset, visual_limit)
        return {"data": result, "public_url": _page_url(result["name"], locale, result.get("game_version")),
                "descriptions": {"public": {"available": bool(descriptions), "items": descriptions},
                                 "system": {"available": bool(reports), "items": reports}}}
    raise ValueError(f"Unknown media tool: {name}")
