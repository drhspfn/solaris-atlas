from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class NodeType(Base):
    __tablename__ = "node_type"
    __table_args__ = ({"schema": "graph"},)
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Node(MetadataMixin, Base):
    __tablename__ = "node"
    __table_args__ = ({"schema": "graph"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    type_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node_type.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    canonical_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)
    slug: Mapped[str | None] = mapped_column(String(512), index=True)
    created_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NodeRevision(MetadataMixin, Base):
    __tablename__ = "node_revision"
    __table_args__ = (
        UniqueConstraint("node_id", "revision", name="uq_node_revision"),
        {"schema": "graph"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="SET NULL")
    )
    revision: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content_hash: Mapped[bytes | None] = mapped_column()
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Edge(MetadataMixin, Base):
    __tablename__ = "edge"
    __table_args__ = (
        UniqueConstraint(
            "from_node_id",
            "relation_type_id",
            "to_node_id",
            "layer",
            "basis",
            name="uq_edge_identity",
        ),
        CheckConstraint("layer IN ('source','canonical','semantic','manual')", name="valid_layer"),
        CheckConstraint(
            "basis IN ('explicit_reference','exact_join','authored_order','source_array_adjacency','semantic_extraction','inference','manual')",
            name="valid_basis",
        ),
        Index("ix_edge_from_relation", "from_node_id", "relation_type_id"),
        Index("ix_edge_to_relation", "to_node_id", "relation_type_id"),
        {"schema": "graph"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    from_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type_id: Mapped[int] = mapped_column(
        ForeignKey("ontology.relation_type.id", ondelete="RESTRICT"), nullable=False
    )
    to_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), nullable=False, index=True
    )
    layer: Mapped[str] = mapped_column(String(24), nullable=False)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    created_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="SET NULL")
    )
    valid_from_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="SET NULL")
    )
    valid_to_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="SET NULL")
    )
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )


class EdgeEvidence(Base):
    __tablename__ = "edge_evidence"
    __table_args__ = (
        UniqueConstraint(
            "edge_id",
            "release_id",
            "source_file_path",
            "source_raw_path",
            postgresql_nulls_not_distinct=True,
            name="uq_edge_evidence_source",
        ),
        {"schema": "graph"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    edge_id: Mapped[int] = mapped_column(
        ForeignKey("graph.edge.id", ondelete="CASCADE"), nullable=False, index=True
    )
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="RESTRICT"), index=True
    )
    evidence_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL"), index=True
    )
    source_file_path: Mapped[str | None] = mapped_column(String(2048))
    source_raw_path: Mapped[str | None] = mapped_column(String(4096))
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
