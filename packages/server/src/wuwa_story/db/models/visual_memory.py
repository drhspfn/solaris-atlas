"""Curated visual identities are separate from authored game entities and lore identity."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base


class VisualLoreIdentity(Base):
    __tablename__ = "visual_lore_identity"
    __table_args__ = ({"schema": "core"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))


class VisualIdentity(Base):
    __tablename__ = "visual_identity"
    __table_args__ = ({"schema": "core"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    lore_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.visual_lore_identity.id", ondelete="SET NULL"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisualIdentityNode(Base):
    __tablename__ = "visual_identity_node"
    __table_args__ = ({"schema": "core"},)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("core.visual_identity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class VisualAppearance(Base):
    __tablename__ = "visual_appearance"
    __table_args__ = (UniqueConstraint("identity_id", "name"), {"schema": "core"})
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("core.visual_identity.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256))


class VisualReference(Base):
    __tablename__ = "visual_reference"
    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected')", name="status_valid"),
        CheckConstraint("retention_mode IN ('image','vector_only')", name="retention_valid"),
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
        CheckConstraint("quality >= 0 AND quality <= 1", name="quality_valid"),
        {"schema": "core"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Hash of the original upload and of the exact crop are different identities.
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    original_file_id: Mapped[int | None] = mapped_column(ForeignKey("storage.file_object.id"))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("storage.file_object.id"))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bbox: Mapped[list[int]] = mapped_column(JSONB)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_page: Mapped[str | None] = mapped_column(Text)
    rights: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(512))
    quality: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", index=True
    )
    enabled_for_search: Mapped[bool] = mapped_column(default=False, server_default="false")
    retention_mode: Mapped[str] = mapped_column(String(16), default="image", server_default="image")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class VisualReferenceLink(Base):
    __tablename__ = "visual_reference_link"
    __table_args__ = ({"schema": "core"},)
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("core.visual_reference.id", ondelete="CASCADE"),
        primary_key=True,
    )
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("core.visual_identity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    appearance_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.visual_appearance.id", ondelete="SET NULL"),
        index=True,
    )


class VisualEmbedding(Base):
    __tablename__ = "visual_embedding"
    __table_args__ = (
        CheckConstraint("vector_dims(embedding) = dimensions", name="dimensions_match"),
        Index("ix_visual_embedding_model_dimensions", "model_id", "dimensions"),
        {"schema": "search"},
    )
    reference_id: Mapped[int] = mapped_column(
        ForeignKey("core.visual_reference.id", ondelete="CASCADE"),
        primary_key=True,
    )
    model_id: Mapped[int] = mapped_column(
        ForeignKey("search.embedding_model.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    dimensions: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisualReview(Base):
    __tablename__ = "visual_review"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    reference_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.visual_reference.id", ondelete="SET NULL"),
        index=True,
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("auth.user.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
