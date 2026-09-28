from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Identity as SQLIdentity,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wuwa_story.db.base import Base, jsonb_default


class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("email_normalized"),
        UniqueConstraint("nickname_normalized"),
        CheckConstraint("role IN (1, 2)", name="role_valid"),
        CheckConstraint("status IN (1, 2)", name="status_valid"),
        {"schema": "auth"},
    )

    id: Mapped[int] = mapped_column(BigInteger, SQLIdentity(always=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    email_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    nickname: Mapped[str] = mapped_column(String(32), nullable=False)
    nickname_normalized: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "session"
    __table_args__ = (
        Index("ix_session_user_id", "user_id"),
        Index("ix_session_expires_at", "expires_at"),
        {"schema": "auth"},
    )

    id: Mapped[int] = mapped_column(BigInteger, SQLIdentity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("auth.user.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class Identity(Base):
    __tablename__ = "identity"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject"),
        Index("ix_identity_user_id", "user_id"),
        CheckConstraint("provider = 1", name="provider_google"),
        {"schema": "auth"},
    )

    id: Mapped[int] = mapped_column(BigInteger, SQLIdentity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("auth.user.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)
    provider_email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=jsonb_default()
    )


class PendingRegistration(Base):
    __tablename__ = "pending_registration"
    __table_args__ = (
        Index("ix_pending_registration_expires_at", "expires_at"),
        {"schema": "auth"},
    )

    id: Mapped[int] = mapped_column(BigInteger, SQLIdentity(always=True), primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    provider: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    email_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_nickname: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=jsonb_default()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingLink(Base):
    __tablename__ = "pending_link"
    __table_args__ = (
        Index("ix_pending_link_expires_at", "expires_at"),
        {"schema": "auth"},
    )

    id: Mapped[int] = mapped_column(BigInteger, SQLIdentity(always=True), primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("auth.user.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)
    provider_email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthState(Base):
    __tablename__ = "oauth_state"
    __table_args__ = (
        CheckConstraint("mode IN ('login', 'link')", name="mode_valid"),
        Index("ix_oauth_state_expires_at", "expires_at"),
        {"schema": "auth"},
    )

    id: Mapped[int] = mapped_column(BigInteger, SQLIdentity(always=True), primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("auth.user.id", ondelete="CASCADE")
    )
    session_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("auth.session.id", ondelete="CASCADE")
    )
    nonce: Mapped[str] = mapped_column(Text, nullable=False)
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    return_to: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'/'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
