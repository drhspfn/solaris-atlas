from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class SourceFile(MetadataMixin, Base):
    __tablename__ = "source_file"
    __table_args__ = (
        UniqueConstraint("release_id", "logical_source_path", name="uq_source_file_release_path"),
        {"schema": "raw"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    release_id: Mapped[int] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    file_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage.file_object.id", ondelete="SET NULL")
    )
    logical_source_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    table_name: Mapped[str | None] = mapped_column(String(512))
    schema_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    row_count: Mapped[int | None] = mapped_column(BigInteger)


class SourceRecord(Base):
    __tablename__ = "source_record"
    __table_args__ = (
        UniqueConstraint("release_id", "source_file_id", "row_index", name="uq_source_record_row"),
        {"schema": "raw"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    release_id: Mapped[int] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_file_id: Mapped[int] = mapped_column(
        ForeignKey("raw.source_file.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    row_index: Mapped[int | None] = mapped_column(BigInteger)
    source_key: Mapped[str | None] = mapped_column(String(1024), index=True)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
