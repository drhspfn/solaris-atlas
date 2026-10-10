import httpx
from typing import Any
import json
import os

from sqlalchemy.ext.asyncio import AsyncSession
from wuwa_story.db.models.search import EmbeddingModel
from sqlalchemy import select

async def generate_query_embedding(session: AsyncSession, text: str) -> list[float]:
    """Generate an embedding for a search query using the active model."""
    model = await session.scalar(select(EmbeddingModel).where(EmbeddingModel.active == True).limit(1))
    if not model:
        raise ValueError("No active embedding model configured in DB")

    api_key = os.environ.get("EMBEDDING_API_KEY", "")
    if not api_key:
        # Fallback to zero vector for testing if no key provided
        return [0.0] * model.dimensions

    base_url = model.config.get("base_url", "")
    
    async with httpx.AsyncClient() as client:
        if model.provider == "gemini":
            url = f"{base_url}/models/{model.model_name}:embedContent?key={api_key}"
            payload = {
                "model": "models/" + model.model_name,
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": model.dimensions,
            }
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["embedding"]["values"]
        else: # OpenAI-like
            url = f"{base_url}/embeddings"
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {
                "model": model.model_name,
                "input": [text],
                "dimensions": model.dimensions,
            }
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
