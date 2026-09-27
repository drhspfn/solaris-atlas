from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, MetadataMixin


class Locale(Base):
    __tablename__ = "locale"
    __table_args__ = ({"schema": "i18n"},)
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class LocalizationKey(MetadataMixin, Base):
    __tablename__ = "localization_key"
    __table_args__ = ({"schema": "i18n"},)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    namespace: Mapped[str | None] = mapped_column(String(128))
    first_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="SET NULL")
    )


class LocalizationValue(Base):
    __tablename__ = "localization_value"
    __table_args__ = (
        UniqueConstraint(
            "release_id", "key_id", "locale_id", name="uq_localization_release_key_locale"
        ),
        {"schema": "i18n"},
    )
    release_id: Mapped[int] = mapped_column(
        ForeignKey("ops.game_release.id", ondelete="RESTRICT"), primary_key=True
    )
    key_id: Mapped[int] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="RESTRICT"), primary_key=True
    )
    locale_id: Mapped[int] = mapped_column(
        ForeignKey("i18n.locale.id", ondelete="RESTRICT"), primary_key=True
    )
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    redirect_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("i18n.localization_key.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw.source_record.id", ondelete="SET NULL")
    )
    content_hash: Mapped[bytes | None] = mapped_column(BYTEA)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
