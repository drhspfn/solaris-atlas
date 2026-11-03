"""Lexical search falls back safely; vectors share the agent embedding adapter."""

import logging
import os

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.providers import Provider
from wuwa_story.agents.settings import AgentSettings
from wuwa_story.db.models.search import EmbeddingModel

logger = logging.getLogger(__name__)


class QueryEmbedding(list[float]):
    def __init__(self, values: list[float], model_id: int) -> None:
        super().__init__(values)
        self.model_id = model_id


async def generate_query_embedding(session: AsyncSession, text: str) -> list[float] | None:
    try:
        model = await session.scalar(select(EmbeddingModel).where(EmbeddingModel.active.is_(True)).order_by(EmbeddingModel.id.desc()).limit(1))
        if model is None:
            return None
        settings = AgentSettings(
            _env_file=None, provider="gemini" if model.provider == "gemini" else "chat",
            base_url=model.config.get("base_url") or "https://api.openai.com/v1",
            api_key=SecretStr(os.environ.get("EMBEDDING_API_KEY", "")),
            embedding_model=model.model_name, embedding_dimensions=model.dimensions,
        )
        async with httpx.AsyncClient() as client:
            vectors, _ = await Provider(settings, client).embed([text])
        return QueryEmbedding(vectors[0], model.id)
    except Exception as error:
        logger.warning("search.embedding_failed type=%s; lexical fallback", type(error).__name__)
        return None
