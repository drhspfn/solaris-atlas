from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base


class AgentJob(Base):
    __tablename__ = "agent_job"
    __table_args__ = ({"schema": "ops"},)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="CASCADE"), primary_key=True
    )
    release_id: Mapped[int] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="RESTRICT"), nullable=False
    )
    locale_id: Mapped[int] = mapped_column(
        ForeignKey("i18n.locale.id", ondelete="RESTRICT"), nullable=False
    )
    identity_hash: Mapped[bytes] = mapped_column(BYTEA, unique=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("content.document.id", ondelete="SET NULL")
    )


class AgentRevisit(Base):
    """Durable import outbox; broker confirmation is not the task identity."""

    __tablename__ = "agent_revisit"
    __table_args__ = (
        UniqueConstraint("document_id", "release_id", name="uq_agent_revisit_source"),
        {"schema": "ops"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("content.document.id", ondelete="CASCADE"), nullable=False
    )
    release_id: Mapped[int] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentDailyUsage(Base):
    __tablename__ = "agent_daily_usage"
    __table_args__ = ({"schema": "ops"},)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal(0), server_default="0"
    )
    spent_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal(0), server_default="0"
    )
    reserved_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    spent_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class AgentCall(Base):
    __tablename__ = "agent_call"
    __table_args__ = (
        UniqueConstraint("run_id", "step", name="uq_agent_call_step"),
        {"schema": "ops"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="RESTRICT"), index=True
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    day: Mapped[date] = mapped_column(
        ForeignKey("ops.agent_daily_usage.day", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentNote(Base):
    __tablename__ = "agent_note"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    release_id: Mapped[int] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="RESTRICT"), nullable=False
    )
    locale_id: Mapped[int] = mapped_column(
        ForeignKey("i18n.locale.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExplanationEmbedding(Base):
    __tablename__ = "explanation_embedding"
    __table_args__ = ({"schema": "search"},)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("content.document.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="RESTRICT"), primary_key=True
    )
    content_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
