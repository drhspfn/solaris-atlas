from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from wuwa_story.api.routes import mcp_character_tools as tools


@pytest.mark.parametrize(
    ("name", "module", "function", "expected"),
    [
        ("get_character_profile", tools.profiles, "character_profile", {"connections_limit": 12, "locale": "en"}),
        ("get_character_archive", tools.character_archive, "character_archive", {"locale": "en"}),
        ("get_character_skills", tools.character_archive, "character_archive", {"locale": "en"}),
        ("get_character_model", tools.character_models, "character_model", {"key": "rover"}),
        ("get_character_connections", tools.character_connections, "character_connections",
         {"section": "quests", "locale": "en", "q": "", "cursor": None, "limit": 12}),
    ],
)
async def test_dispatch_forwards_explicit_defaults_and_session(monkeypatch, name, module, function, expected):
    session = object()
    handler = AsyncMock(return_value={"canonical_key": "character:1", "skills": [],
                                      "source": {"raw": "secret", "confidence": "high"}})
    monkeypatch.setattr(module, function, handler)
    result = await tools.dispatch(name, {"canonical_key": "rover"}, session)
    assert result["source"] == {"confidence": "high"}
    kwargs = handler.await_args.kwargs
    assert kwargs["session"] is session
    assert kwargs["game_version"] is None
    assert all(kwargs[key] == value for key, value in expected.items())
    assert kwargs.get("canonical_key", kwargs.get("key")) == "rover"


async def test_skills_preserves_scaling_and_removes_provenance(monkeypatch):
    handler = AsyncMock(return_value={"canonical_key": "character:1", "locale": "ja", "release_id": 5,
                                      "skills": [{"attributes": [{"values": [1, 2], "source": {"file": "secret", "row": 3, "confidence": "high"}}],
                                                  "icon": {"engine_path": "secret", "url": "public"}}],
                                      "skill_tree": [{"costs": [{"item_id": 1, "count": 3}]}],
                                      "biography": "other"})
    monkeypatch.setattr(tools.character_archive, "character_archive", handler)
    session = object()
    result = await tools.dispatch("get_character_skills", {"canonical_key": "character:1", "locale": "ja",
                                                           "game_version": "2.0"}, session)
    assert result["skills"] == [{"attributes": [{"values": [1, 2], "source": {"confidence": "high"}}], "icon": {"url": "public"}}]
    assert result["skill_tree"][0]["costs"] == [{"item_id": 1, "count": 3}]
    assert "biography" not in result
    assert handler.await_args.kwargs["game_version"] == "2.0"


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": 51}, {"limit": True}, {"q": "x" * 201},
                                   {"cursor": "x" * 2049}, {"section": "other"}, {"extra": 1}])
async def test_invalid_connections_arguments_rejected(args):
    with pytest.raises(ValidationError):
        await tools.dispatch("get_character_connections", {"canonical_key": "rover", **args}, object())


async def test_unknown_tool_and_blank_key_rejected():
    with pytest.raises(ValueError):
        await tools.dispatch("unknown", {}, object())
    with pytest.raises(ValueError):
        await tools.dispatch("get_character_profile", {"canonical_key": "  "}, object())


def test_tool_schemas_advertise_bounds_and_required_key():
    assert len(tools.TOOLS) == 5
    for tool in tools.TOOLS:
        assert tool.inputSchema["required"] == ["canonical_key"]
    connections = next(tool for tool in tools.TOOLS if tool.name == "get_character_connections")
    assert connections.inputSchema["properties"]["limit"]["maximum"] == 50
