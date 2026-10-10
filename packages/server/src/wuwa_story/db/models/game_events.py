"""Versioned in-game event archive and its dated schedule entries."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base


class GameEvent(Base):
    __tablename__ = "game_event"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_game_event_source_identity"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    group_label_key: Mapped[str | None] = mapped_column(String(256))
    game_path: Mapped[str | None] = mapped_column(String(512))
    banner_path: Mapped[str | None] = mapped_column(String(1024))
    rewards: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    source_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GameEventOccurrence(Base):
    __tablename__ = "game_event_occurrence"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "source_occurrence_id", "server", name="uq_game_event_occurrence"
        ),
        Index("ix_game_event_occurrence_dates", "starts_at", "ends_at"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("core.game_event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_occurrence_id: Mapped[str] = mapped_column(String(256), nullable=False)
    game_version: Mapped[str | None] = mapped_column(String(32))
    server: Mapped[str] = mapped_column(String(32), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    banner_path: Mapped[str | None] = mapped_column(String(1024))
    rewards: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    season: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
