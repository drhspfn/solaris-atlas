from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class RelationType(MetadataMixin, Base):
    __tablename__ = "relation_type"
    __table_args__ = ({"schema": "ontology"},)
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    inverse_relation_id: Mapped[int | None] = mapped_column(
        ForeignKey("ontology.relation_type.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="general", server_default="general"
    )
    directional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RelationAlias(Base):
    __tablename__ = "relation_alias"
    __table_args__ = (
        UniqueConstraint(
            "relation_type_id",
            "locale_id",
            "normalized_alias",
            postgresql_nulls_not_distinct=True,
            name="uq_relation_alias",
        ),
        {"schema": "ontology"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    relation_type_id: Mapped[int] = mapped_column(
        ForeignKey("ontology.relation_type.id", ondelete="CASCADE"), nullable=False
    )
    locale_id: Mapped[int | None] = mapped_column(ForeignKey("i18n.locale.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(256), nullable=False, index=True)


class Tag(MetadataMixin, Base):
    __tablename__ = "tag"
    __table_args__ = ({"schema": "ontology"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class NodeTag(Base):
    __tablename__ = "node_tag"
    __table_args__ = ({"schema": "ontology"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("ontology.tag.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )


class TagAlias(Base):
    __tablename__ = "tag_alias"
    __table_args__ = (
        UniqueConstraint(
            "tag_id",
            "locale_id",
            "normalized_alias",
            postgresql_nulls_not_distinct=True,
            name="uq_tag_alias",
        ),
        {"schema": "ontology"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("ontology.tag.id", ondelete="CASCADE"), nullable=False
    )
    locale_id: Mapped[int | None] = mapped_column(ForeignKey("i18n.locale.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(256), nullable=False, index=True)


class EventType(Base):
    __tablename__ = "event_type"
    __table_args__ = ({"schema": "ontology"},)
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class RelationCandidate(MetadataMixin, Base):
    __tablename__ = "relation_candidate"
    __table_args__ = ({"schema": "ontology"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    proposed_key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sample_subject_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL")
    )
    sample_object_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL")
    )
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )


class TagCandidate(MetadataMixin, Base):
    __tablename__ = "tag_candidate"
    __table_args__ = ({"schema": "ontology"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    proposed_key: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
