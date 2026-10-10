import logging
from dataclasses import dataclass
from typing import Any, List

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.lore import LoreChunk, LoreChunkEmbedding
from wuwa_story.db.models.search import EmbeddingModel

logger = logging.getLogger(__name__)

@dataclass
class SearchResult:
    chunk_id: int
    source_type: str
    source_id: str
    quest_id: str | None
    chunk_type: str
    content: str
    score: float

class LoreSearchService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def hybrid_search(self, query: str, query_embedding: list[float], limit: int = 30) -> List[SearchResult]:
        # Implementation of Reciprocal Rank Fusion (RRF) between Full-text Search and Vector Search
        
        # 1. We get the active embedding model ID
        model_id = await self.session.scalar(select(EmbeddingModel.id).where(EmbeddingModel.active == True).limit(1))
        if not model_id:
            logger.warning("No active embedding model found for vector search")
            return []

        # Vector search query
        # We use pgvector's <=> operator (cosine distance). The smaller the distance, the more similar.
        # RRF formula: 1 / (rank + k), where k is typically 60.
        
        sql = """
        WITH vector_search AS (
            SELECT 
                c.id, 
                c.source_type, 
                c.source_id, 
                c.quest_id, 
                c.chunk_type, 
                c.content,
                e.embedding <=> :query_embedding::vector AS distance,
                RANK() OVER (ORDER BY e.embedding <=> :query_embedding::vector) AS rank
            FROM search.lore_chunk c
            JOIN search.lore_chunk_embedding e ON e.chunk_id = c.id
            WHERE e.model_id = :model_id
            ORDER BY distance
            LIMIT :limit
        ),
        fts_search AS (
            SELECT 
                c.id, 
                c.source_type, 
                c.source_id, 
                c.quest_id, 
                c.chunk_type, 
                c.content,
                ts_rank(c.search_vector, websearch_to_tsquery('english', :query)) AS rank_score,
                RANK() OVER (ORDER BY ts_rank(c.search_vector, websearch_to_tsquery('english', :query)) DESC) AS rank
            FROM search.lore_chunk c
            WHERE c.search_vector @@ websearch_to_tsquery('english', :query)
            ORDER BY rank_score DESC
            LIMIT :limit
        ),
        rrf AS (
            SELECT 
                COALESCE(v.id, f.id) AS chunk_id,
                COALESCE(v.source_type, f.source_type) AS source_type,
                COALESCE(v.source_id, f.source_id) AS source_id,
                COALESCE(v.quest_id, f.quest_id) AS quest_id,
                COALESCE(v.chunk_type, f.chunk_type) AS chunk_type,
                COALESCE(v.content, f.content) AS content,
                (
                    COALESCE(1.0 / (60 + v.rank), 0.0) + 
                    COALESCE(1.0 / (60 + f.rank), 0.0)
                ) AS score
            FROM vector_search v
            FULL OUTER JOIN fts_search f ON v.id = f.id
        )
        SELECT * FROM rrf 
        ORDER BY score DESC 
        LIMIT :limit
        """

        result = await self.session.execute(
            text(sql), 
            {
                "query": query, 
                "query_embedding": query_embedding, 
                "model_id": model_id,
                "limit": limit
            }
        )

        rows = result.mappings().all()
        return [
            SearchResult(
                chunk_id=row["chunk_id"],
                source_type=row["source_type"],
                source_id=row["source_id"],
                quest_id=row["quest_id"],
                chunk_type=row["chunk_type"],
                content=row["content"],
                score=float(row["score"])
            )
            for row in rows
        ]
