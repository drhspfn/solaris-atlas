"""Agent workspace data, separate from immutable imported game sources."""

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
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base


class AgentToolAudit(Base):
    __tablename__ = "agent_tool_audit"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL"), index=True
    )
    step: Mapped[int | None] = mapped_column(Integer)
    call_id: Mapped[str] = mapped_column(String(256), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AgentMemory(Base):
    __tablename__ = "agent_memory"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL"), index=True
    )
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentMemoryEmbedding(Base):
    __tablename__ = "agent_memory_embedding"
    __table_args__ = ({"schema": "search"},)
    memory_id: Mapped[int] = mapped_column(
        ForeignKey("ops.agent_memory.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AgentWorkItem(Base):
    __tablename__ = "agent_work_item"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("content.document.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL"), index=True
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="open", index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    search_terms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list)
    resolution: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempted_corpus: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentFactBoard(Base):
    """Shared structured fact & relationship board across quest, visual, and research agents."""

    __tablename__ = "agent_fact_board"
    __table_args__ = (
        Index("ix_agent_fact_board_type_status", "fact_type", "status"),
        Index("ix_agent_fact_board_importance", "importance"),
        {"schema": "ops"},
    )
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    fact_type: Mapped[str] = mapped_column(
        String(48), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3)
    confidence: Mapped[str] = mapped_column(
        String(24), nullable=False, default="observed")
    source_kind: Mapped[str] = mapped_column(
        String(48), nullable=False, default="quest_analysis")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", index=True)
    subject_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL"), index=True
    )
    object_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL"), index=True
    )
    relation_type: Mapped[str | None] = mapped_column(String(64))
    quest_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL"), index=True
    )
    cutscene_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL"), index=True
    )
    question_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.agent_work_item.id", ondelete="SET NULL"), index=True
    )
    note_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.agent_note.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL"), index=True
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
