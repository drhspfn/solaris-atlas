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


class EntityAlias(Base):
    __tablename__ = "entity_alias"
    __table_args__ = (
        UniqueConstraint(
            "node_id", "locale_id", "normalized_alias", "alias_type", name="uq_entity_alias"
        ),
        Index(
            "ix_entity_alias_trgm",
            "normalized_alias",
            postgresql_using="gin",
            postgresql_ops={"normalized_alias": "gin_trgm_ops"},
        ),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    locale_id: Mapped[int | None] = mapped_column(ForeignKey("i18n.locale.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    alias_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class SearchDocument(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint(
            "target_node_id",
            "category",
            "locale_id",
            postgresql_nulls_not_distinct=True,
            name="uq_search_document",
        ),
        Index("ix_search_document_fts", "search_vector", postgresql_using="gin"),
        Index(
            "ix_search_document_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    target_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    locale_id: Mapped[int | None] = mapped_column(ForeignKey("i18n.locale.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EmbeddingModel(Base):
    __tablename__ = "embedding_model"
    __table_args__ = ({"schema": "search"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_metric: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")


class NodeEmbedding(Base):
    __tablename__ = "node_embedding"
    __table_args__ = (
        UniqueConstraint(
            "node_id",
            "embedding_kind",
            "locale_id",
            "model_id",
            postgresql_nulls_not_distinct=True,
            name="uq_node_embedding",
        ),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    locale_id: Mapped[int | None] = mapped_column(ForeignKey("i18n.locale.id", ondelete="CASCADE"))
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelationEmbedding(Base):
    __tablename__ = "relation_embedding"
    __table_args__ = (
        UniqueConstraint("relation_type_id", "model_id", name="uq_relation_embedding"),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    relation_type_id: Mapped[int] = mapped_column(
        ForeignKey("ontology.relation_type.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)


class TagEmbedding(Base):
    __tablename__ = "tag_embedding"
    __table_args__ = (
        UniqueConstraint("tag_id", "model_id", name="uq_tag_embedding"),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("ontology.tag.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)


class DialogueChunk(Base):
    __tablename__ = "dialogue_chunk"
    __table_args__ = (
        UniqueConstraint(
            "scene_node_id",
            "conversation_node_id",
            "ordinal",
            "locale_id",
            postgresql_nulls_not_distinct=True,
            name="uq_dialogue_chunk",
        ),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    scene_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("story.scene.node_id", ondelete="CASCADE")
    )
    conversation_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.conversation.node_id", ondelete="CASCADE")
    )
    start_line_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.dialogue_line.node_id", ondelete="SET NULL")
    )
    end_line_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.dialogue_line.node_id", ondelete="SET NULL")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    locale_id: Mapped[int] = mapped_column(
        ForeignKey("i18n.locale.id", ondelete="RESTRICT"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)


class DialogueChunkEmbedding(Base):
    __tablename__ = "dialogue_chunk_embedding"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model_id", name="uq_dialogue_chunk_embedding"),
        {"schema": "search"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("search.dialogue_chunk.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
