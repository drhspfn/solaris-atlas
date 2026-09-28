from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class FileType(Base):
    __tablename__ = "file_type"
    __table_args__ = ({"schema": "storage"},)
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(256))


class FileObject(MetadataMixin, Base):
    __tablename__ = "file_object"
    __table_args__ = (
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="nonnegative_size"),
        {"schema": "storage"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    file_type_id: Mapped[int] = mapped_column(
        ForeignKey("storage.file_type.id", ondelete="RESTRICT"), nullable=False
    )
    logical_name: Mapped[str | None] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[bytes | None] = mapped_column(BYTEA, unique=True, index=True)
    etag: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FileLocation(MetadataMixin, Base):
    __tablename__ = "file_location"
    __table_args__ = (
        UniqueConstraint(
            "backend",
            "bucket",
            "object_key",
            postgresql_nulls_not_distinct=True,
            name="uq_file_location_identity",
        ),
        Index(
            "uq_file_location_primary", "file_id", unique=True, postgresql_where=text("is_primary")
        ),
        {"schema": "storage"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("storage.file_object.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket: Mapped[str | None] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    region: Mapped[str | None] = mapped_column(String(128))
    storage_class: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(256))
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FileReference(MetadataMixin, Base):
    __tablename__ = "file_reference"
    __table_args__ = ({"schema": "storage"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="SET NULL"), index=True
    )
    owner_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL"), index=True
    )
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage.file_object.id", ondelete="SET NULL"), index=True
    )
    reference_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(1024))
    source_path: Mapped[str | None] = mapped_column(String(2048))
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )


class FileVariant(MetadataMixin, Base):
    __tablename__ = "file_variant"
    __table_args__ = ({"schema": "storage"},)
    parent_file_id: Mapped[int] = mapped_column(
        ForeignKey("storage.file_object.id", ondelete="CASCADE"), primary_key=True
    )
    child_file_id: Mapped[int] = mapped_column(
        ForeignKey("storage.file_object.id", ondelete="CASCADE"), primary_key=True
    )
    variant_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )
