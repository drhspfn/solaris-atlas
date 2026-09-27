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


class GameRelease(MetadataMixin, Base):
    __tablename__ = "game_release"
    __table_args__ = (
        UniqueConstraint(
            "game_version",
            "resource_version",
            "upstream_name",
            "upstream_commit",
            postgresql_nulls_not_distinct=True,
            name="uq_release_identity",
        ),
        {"schema": "ops"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    game_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_version: Mapped[str | None] = mapped_column(String(64))
    changelist: Mapped[int | None] = mapped_column(BigInteger)
    upstream_name: Mapped[str] = mapped_column(String(256), nullable=False)
    upstream_commit: Mapped[str | None] = mapped_column(String(128))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImportRun(MetadataMixin, Base):
    __tablename__ = "import_run"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    release_id: Mapped[int] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    importer: Mapped[str] = mapped_column(String(128), nullable=False)
    importer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    records_seen: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    records_created: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    records_updated: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    records_failed: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    error: Mapped[str | None] = mapped_column(Text)


class Processor(MetadataMixin, Base):
    __tablename__ = "processor"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class AIModel(MetadataMixin, Base):
    __tablename__ = "ai_model"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProcessingRun(MetadataMixin, Base):
    __tablename__ = "processing_run"
    __table_args__ = ({"schema": "ops"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    processor_id: Mapped[int] = mapped_column(
        ForeignKey("ops.processor.id", ondelete="RESTRICT"), nullable=False
    )
    model_id: Mapped[int | None] = mapped_column(ForeignKey("ops.ai_model.id", ondelete="SET NULL"))
    target_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL")
    )
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[bytes | None] = mapped_column(BYTEA)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    tokens_input: Mapped[int | None] = mapped_column(BigInteger)
    tokens_output: Mapped[int | None] = mapped_column(BigInteger)
    cost: Mapped[float | None] = mapped_column()
    raw_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Dependency(Base):
    __tablename__ = "dependency"
    __table_args__ = ({"schema": "ops"},)
    from_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    to_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    dependency_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
