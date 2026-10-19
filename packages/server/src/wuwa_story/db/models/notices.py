"""Archived public in-game announcements, including withdrawn entries and revisions."""

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


class GameNotice(Base):
    __tablename__ = "game_notice"
    __table_args__ = (
        UniqueConstraint("feed", "locale", "source_id", name="uq_game_notice_identity"),
        Index("uq_game_notice_slug", "slug", unique=True),
        {"schema": "core"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    feed: Mapped[str] = mapped_column(String(256), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str | None] = mapped_column(String(512))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    related_event_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GameNoticeRevision(Base):
    __tablename__ = "game_notice_revision"
    __table_args__ = ({"schema": "core"},)
    notice_id: Mapped[int] = mapped_column(
        ForeignKey("core.game_notice.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
