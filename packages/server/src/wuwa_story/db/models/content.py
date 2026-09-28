from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class Document(MetadataMixin, Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint(
            "node_id", "locale_id", "document_type", "revision", name="uq_content_document_revision"
        ),
        {"schema": "content"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    locale_id: Mapped[int | None] = mapped_column(ForeignKey("i18n.locale.id", ondelete="SET NULL"))
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(512))
    plain_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_ast: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentHead(Base):
    __tablename__ = "document_head"
    __table_args__ = (
        UniqueConstraint(
            "node_id",
            "locale_id",
            "document_type",
            postgresql_nulls_not_distinct=True,
            name="uq_document_head_identity",
        ),
        {"schema": "content"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False
    )
    locale_id: Mapped[int | None] = mapped_column(ForeignKey("i18n.locale.id", ondelete="CASCADE"))
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("content.document.id", ondelete="CASCADE"), nullable=False
    )


class DocumentReference(Base):
    __tablename__ = "document_reference"
    __table_args__ = ({"schema": "content"},)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("content.document.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), index=True
    )
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("story.claim.id", ondelete="CASCADE"))
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str | None] = mapped_column(String(512))
