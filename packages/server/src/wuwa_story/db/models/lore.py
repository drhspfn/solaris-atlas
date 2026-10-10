from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base

class LoreChunk(Base):
    __tablename__ = "lore_chunk"
    __table_args__ = (
        Index("ix_lore_chunk_fts", "search_vector", postgresql_using="gin"),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    quest_id: Mapped[str | None] = mapped_column(String(100))
    game_version: Mapped[str | None] = mapped_column(String(20))
    characters: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    chunk_type: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str | None] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)
    timestamp_start: Mapped[float | None] = mapped_column(Integer)
    timestamp_end: Mapped[float | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

class LoreChunkEmbedding(Base):
    __tablename__ = "lore_chunk_embedding"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model_id", name="uq_lore_chunk_embedding"),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("search.lore_chunk.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
