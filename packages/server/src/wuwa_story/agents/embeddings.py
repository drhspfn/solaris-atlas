"""One embedding protocol and model identity for explanations, search and memory."""

import hashlib
import json
from typing import Any

from wuwa_story.agents.settings import AgentSettings


def embedding_settings(settings: AgentSettings) -> AgentSettings:
    return settings.model_copy(
        update={
            "provider": settings.embedding_provider or settings.provider,
            "base_url": settings.embedding_base_url or settings.base_url,
            "api_key": settings.embedding_api_key
            if settings.embedding_api_key.get_secret_value()
            else settings.api_key,
        }
    )


def model_identity(settings: AgentSettings) -> dict[str, Any]:
    settings = embedding_settings(settings)
    return {
        "provider": settings.provider,
        "base_url": settings.base_url,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }


def model_key(settings: AgentSettings) -> str:
    return (
        "story:"
        + hashlib.sha256(
            json.dumps(model_identity(settings), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    )


def embedding_request(settings: AgentSettings, texts: list[str]) -> tuple[str, dict[str, Any]]:
    settings = embedding_settings(settings)
    if settings.provider == "gemini":
        if len(texts) != 1:
            raise ValueError("Gemini embedding requires a single input")
        return f"/models/{settings.embedding_model}:embedContent", {
            "model": "models/" + settings.embedding_model,
            "content": {"parts": [{"text": texts[0]}]},
            "outputDimensionality": settings.embedding_dimensions,
        }
    return "/embeddings", {
        "model": settings.embedding_model,
        "input": texts,
        "dimensions": settings.embedding_dimensions,
    }


def embedding_values(settings: AgentSettings, raw: dict[str, Any]) -> list[list[float]]:
    from wuwa_story.agents.providers import vector_values

    settings = embedding_settings(settings)
    values = (
        [raw["embedding"]["values"]]
        if settings.provider == "gemini"
        else [row["embedding"] for row in sorted(raw["data"], key=lambda row: row["index"])]
    )
    return [vector_values(value, settings.embedding_dimensions) for value in values]
