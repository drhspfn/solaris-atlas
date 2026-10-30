"""Transactional dirty scopes for durable public read projections."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base

READ_PROJECTION_KINDS = (
    "character", "item", "location", "quest", "game_event", "game_notice",
    "speaker", "dialogue", "cutscene", "story_map",
)
HISTORICAL_READ_PROJECTION_KINDS = tuple(
    kind for kind in READ_PROJECTION_KINDS if kind not in {"location", "speaker", "game_notice"}
)


class ReadProjectionDirty(Base):
    """Coalesced work; entity_id=0 rebuilds a kind within snapshot_key.

    Invalidations increment requested_generation without clearing an active lease.
    Claims return that generation and commit before building. Publishing locks
    this row and checks the token, lease expiry and captured generation before
    writing projections and advancing processed_generation in one transaction.
    Tokens also fence lease renewal/release, including stale-build cleanup.
    """

    __tablename__ = "read_projection_dirty"
    __table_args__ = (
        CheckConstraint("entity_id >= 0", name="nonnegative_entity_id"),
        CheckConstraint(
            "requested_generation >= 1 AND processed_generation >= 0 "
            "AND processed_generation <= requested_generation",
            name="valid_generations",
        ),
        CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
        CheckConstraint(
            "(lease_until IS NULL) = (lease_token IS NULL)", name="paired_lease",
        ),
        Index(
            "ix_read_projection_dirty_pending", "available_at", "lease_until",
            postgresql_where=text("requested_generation > processed_generation"),
        ),
        Index(
            "ix_read_projection_dirty_scope_pending", "kind", "snapshot_key",
            postgresql_where=text("requested_generation > processed_generation"),
        ),
        {"schema": "ops"},
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, default=0, server_default="0",
    )
    snapshot_key: Mapped[str] = mapped_column(
        String(256), primary_key=True, default="current", server_default="current",
    )
    requested_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default="1",
    )
    processed_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0",
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class ReadProjectionScope(Base):
    """Coverage, including empty catalogs; generations are independent of entries.

    First publication requires complete bulk coverage. published_generation > 0
    remains available while subsequent writes mark refresh status pending.
    Workers lock this scope before dirty rows and publish coverage with entries.
    """

    __tablename__ = "read_projection_scope"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="positive_generation"),
        CheckConstraint("status IN ('pending', 'building', 'ready')", name="valid_status"),
        CheckConstraint("published_generation >= 0 AND published_generation <= generation", name="valid_published_generation"),
        CheckConstraint("(published_generation = 0) = (published_at IS NULL)", name="paired_publication"),
        {"schema": "ops"},
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    published_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
