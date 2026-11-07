"""Standalone character MCP tools; callers own the session and response encoding."""

from typing import Any, Literal

from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes import character_models
from wuwa_story.api.routes.story import character_archive, character_connections, profiles


class CharacterArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    canonical_key: str = Field(min_length=1, max_length=512, description="Character canonical key or public slug")
    game_version: str | None = Field(default=None, min_length=1, max_length=100)


class LocalizedArgs(CharacterArgs):
    locale: str = Field(default="en", min_length=1, max_length=50)


class ProfileArgs(LocalizedArgs):
    connections_limit: int = Field(default=12, ge=1, le=50)


class ConnectionsArgs(LocalizedArgs):
    section: Literal["quests", "characters"] = "quests"
    q: str = Field(default="", max_length=200)
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=12, ge=1, le=50)


_DEFINITIONS = {
    "get_character_profile": (ProfileArgs, "Get a character profile with bounded connection previews."),
    "get_character_archive": (LocalizedArgs, "Get a character's localized archive."),
    "get_character_skills": (LocalizedArgs, "Get skills, per-level scaling, skill-tree costs, and resonance chains."),
    "get_character_model": (CharacterArgs, "Get a character's public model for a game version."),
    "get_character_connections": (ConnectionsArgs, "Search and paginate a character's quest or character connections."),
}

TOOLS = [Tool(name=name, description=description, inputSchema=model.model_json_schema())
         for name, (model, description) in _DEFINITIONS.items()]


def _public(value: Any, in_source: bool = False) -> Any:
    """Remove raw source records and internal asset paths, retaining provenance context."""
    if isinstance(value, list):
        return [_public(item, in_source) for item in value]
    if isinstance(value, dict):
        return {key: _public(item, in_source or key in {"source", "sources", "provenance"})
                for key, item in value.items()
                if key not in {"raw", "raw_record", "metadata", "metadata_json", "engine_path",
                               "audio_event_path"}
                and not (in_source and key in {"file", "row"})
                and not key.startswith("raw_") and not key.endswith(("source_record_id", "source_file", "source_row"))}
    return value


async def dispatch(name: str, arguments: dict[str, Any], session: AsyncSession) -> Any:
    if name not in _DEFINITIONS:
        raise ValueError(f"Unknown character tool: {name}")
    args = _DEFINITIONS[name][0].model_validate(arguments).model_dump()
    if not args["canonical_key"].strip():
        raise ValueError("canonical_key must not be blank")

    if name == "get_character_profile":
        result = await profiles.character_profile(**args, session=session)
    elif name in {"get_character_archive", "get_character_skills"}:
        result = await character_archive.character_archive(**args, session=session)
        if name == "get_character_skills":
            result = {key: result[key] for key in (
                "canonical_key", "locale", "release_id", "source", "skills", "skill_tree", "resonance_chain"
            ) if key in result}
    elif name == "get_character_model":
        result = await character_models.character_model(
            key=args["canonical_key"], game_version=args["game_version"], session=session)
    else:
        result = await character_connections.character_connections(**args, session=session)
    return _public(result)
