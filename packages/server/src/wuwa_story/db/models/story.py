from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class Scene(MetadataMixin, Base):
    __tablename__ = "scene"
    __table_args__ = ({"schema": "story"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    quest_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.quest.node_id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(String(512))
    authored_order: Mapped[int | None] = mapped_column(Integer)
    source_basis: Mapped[str] = mapped_column(String(32), nullable=False)


class Event(MetadataMixin, Base):
    __tablename__ = "event"
    __table_args__ = ({"schema": "story"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    event_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("ontology.event_type.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    importance: Mapped[int | None] = mapped_column(SmallInteger)
    start_order: Mapped[int | None] = mapped_column(Integer)
    end_order: Mapped[int | None] = mapped_column(Integer)
    semantic_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="dirty", server_default="dirty"
    )
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )


class StoryContainer(MetadataMixin, Base):
    __tablename__ = "story_container"
    __table_args__ = ({"schema": "story"},)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    container_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    importance: Mapped[int | None] = mapped_column(SmallInteger)


class StoryMembership(MetadataMixin, Base):
    __tablename__ = "story_membership"
    __table_args__ = (
        UniqueConstraint(
            "parent_node_id", "child_node_id", "membership_type", name="uq_story_membership"
        ),
        {"schema": "story"},
    )
    parent_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    child_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    membership_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_index: Mapped[int | None] = mapped_column(Integer)


class Claim(MetadataMixin, Base):
    __tablename__ = "claim"
    __table_args__ = ({"schema": "story"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    subject_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL"), index=True
    )
    predicate_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("ontology.relation_type.id", ondelete="SET NULL"), index=True
    )
    object_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("graph.node.id", ondelete="SET NULL"), index=True
    )
    object_text: Mapped[str | None] = mapped_column(Text)
    claim_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    processor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.processing_run.id", ondelete="SET NULL")
    )


class ClaimEvidence(MetadataMixin, Base):
    __tablename__ = "claim_evidence"
    __table_args__ = ({"schema": "story"},)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("story.claim.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_node_id: Mapped[int] = mapped_column(
        ForeignKey("graph.node.id", ondelete="CASCADE"), primary_key=True
    )
    relevance: Mapped[float | None] = mapped_column()
