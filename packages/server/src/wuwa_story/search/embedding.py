"""Lexical search falls back safely; vectors share the agent embedding adapter."""

import logging
import os

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.agents.workspace import paid_vector
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
        live = get_agent_settings()
        settings = live.model_copy(update={
            "embedding_provider": "gemini" if model.provider == "gemini" else "chat",
            "embedding_base_url": model.config.get("base_url") or live.base_url,
            "embedding_api_key": SecretStr(os.environ.get("EMBEDDING_API_KEY", "")) if os.environ.get("EMBEDDING_API_KEY") else live.embedding_api_key,
            "embedding_model": model.model_name, "embedding_dimensions": model.dimensions,
        })
        vector = await paid_vector(session, settings, text, existing_model_id=model.id)
        return QueryEmbedding(vector[1], model.id) if vector else None
    except Exception as error:
        logger.warning("search.embedding_failed type=%s; lexical fallback", type(error).__name__)
        return None
