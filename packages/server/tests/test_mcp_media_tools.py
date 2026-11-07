from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from wuwa_story.api.routes import mcp_media_tools as tools


def test_tool_contract():
    assert {tool.name for tool in tools.TOOLS} == {
        "get_item_profile", "get_item_sellers", "search_cutscenes", "get_cutscene"
    }


@pytest.mark.asyncio
async def test_item_routes_preserve_values_and_errors(monkeypatch):
    profile = {"source": {"certainty": "inferred"}}
    monkeypatch.setattr(tools.profiles, "item_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(tools.npc_sales, "item_npc_sellers", AsyncMock(side_effect=HTTPException(404)))
    assert await tools.dispatch("get_item_profile", {"canonical_key": "item:3", "locale": "ja", "game_version": "2"}, "s") is profile
    tools.profiles.item_profile.assert_awaited_once_with(canonical_key="item:3", locale="ja", game_version="2", session="s")
    with pytest.raises(HTTPException):
        await tools.dispatch("get_item_sellers", {"item_id": 3, "game_version": "old"}, "s")
    tools.npc_sales.item_npc_sellers.assert_awaited_once_with(item_id=3, game_version="old", locale="en", limit=50, offset=0, session="s")


@pytest.mark.asyncio
async def test_search_and_detail_preserve_data_and_public_urls(monkeypatch):
    description = {"text": "A scene", "visual_reference_id": 4}
    payload = {"name": "scene / 1", "game_version": "2.0", "event": {"playback": {"media": {"asset": {"url": "https://cdn/video", "description": description}}}}, "passages": [{"context_inferred": True}]}
    monkeypatch.setattr(tools.cutscenes, "detail", AsyncMock(return_value=payload))
    monkeypatch.setattr(tools.cutscenes, "catalog", AsyncMock(return_value={"items": [{"name": "scene / 1", "game_version": "2.0", "title_kind": "analysis"}], "next_cursor": "next"}))
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(frontend_url="https://example.test/"))
    url = "https://example.test/cutscenes/scene%20%2F%201?locale=ja&game_version=2.0"
    result = await tools.dispatch("get_cutscene", {"name": "scene / 1", "locale": "ja"}, "s")
    assert result["data"] is payload
    assert result["public_url"] == url
    assert result["descriptions"]["public"] == {"available": True, "items": [description]}
    assert result["descriptions"]["system"]["available"] is False
    search = await tools.dispatch("search_cutscenes", {"locale": "ja", "cursor": "next", "playable": False}, "s")
    assert search["items"][0]["public_url"] == url
    assert search["items"][0]["title_kind"] == "analysis"
    tools.cutscenes.catalog.assert_awaited_once_with(game_version=None, locale="ja", q="", playable=False, limit=24, offset=0, cursor="next", session="s")
    with pytest.raises(ValueError):
        await tools.dispatch("search_cutscenes", {"limit": 49}, "s")


@pytest.mark.asyncio
async def test_missing_description_and_unknown_tool(monkeypatch):
    monkeypatch.setattr(tools.cutscenes, "detail", AsyncMock(return_value={"name": "scene", "event": {"playback": None}}))
    result = await tools.dispatch("get_cutscene", {"name": "scene"}, "s")
    assert result["descriptions"]["public"] == {"available": False, "items": []}
    with pytest.raises(ValueError, match="Unknown media tool"):
        await tools.dispatch("unknown", {}, "s")
