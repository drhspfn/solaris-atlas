"""Durable public read projections, populated by generation-scoped refreshes.

Entity identities belong to their kind, not necessarily graph.node. A snapshot
is either ``current`` or an explicitly published version; readers never infer it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base


class _EntityReadMixin:
    __table_args__ = {"schema": "core"}

    entity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    locale_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    snapshot_key: Mapped[str] = mapped_column(
        String(64), primary_key=True, default="current", server_default=text("'current'")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sort_title: Mapped[str] = mapped_column(Text, nullable=False)
    # Events/notices can have public addresses without a graph identity.
    canonical_key: Mapped[str | None] = mapped_column(String(512))
    slug: Mapped[str | None] = mapped_column(String(512))
    primary_image_file_id: Mapped[int | None] = mapped_column(BigInteger)
    primary_image_url: Mapped[str | None] = mapped_column(Text)
    artwork: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True), nullable=False)
    # Required: a writer must stamp the dirty-scope generation it projected.
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CharacterRead(_EntityReadMixin, Base):
    __tablename__ = "character_read"


class ItemRead(_EntityReadMixin, Base):
    __tablename__ = "item_read"


class LocationRead(_EntityReadMixin, Base):
    __tablename__ = "location_read"


class QuestRead(_EntityReadMixin, Base):
    __tablename__ = "quest_read"


class GameEventRead(_EntityReadMixin, Base):
    __tablename__ = "game_event_read"


class GameNoticeRead(_EntityReadMixin, Base):
    __tablename__ = "game_notice_read"


class SpeakerRead(_EntityReadMixin, Base):
    __tablename__ = "speaker_read"


class DialogueRead(_EntityReadMixin, Base):
    __tablename__ = "dialogue_read"


class CutsceneRead(_EntityReadMixin, Base):
    __tablename__ = "cutscene_read"


class EntityReadRelation(Base):
    """Directed projected relations; multiple evidence records share one row."""

    __tablename__ = "entity_read_relation"
    __table_args__ = {"schema": "core"}

    owner_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    related_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    related_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    relation_type: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_key: Mapped[str] = mapped_column(
        String(64), primary_key=True, default="current", server_default=text("'current'")
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
