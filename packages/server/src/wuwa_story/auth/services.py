import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.constants import IdentityProvider, UserRole, UserStatus
from wuwa_story.auth.security import (
    hash_password,
    new_secret,
    token_digest,
    verify_password_and_update,
)
from wuwa_story.auth.validation import (
    ValidationError,
    normalize_email,
    validate_nickname,
    validate_password,
)
from wuwa_story.config.settings import Settings
from wuwa_story.db.models.auth import (
    AuthSession,
    Identity,
    OAuthState,
    PendingLink,
    PendingRegistration,
    User,
)

logger = logging.getLogger("wuwa_story.auth")


class AuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def now_utc() -> datetime:
    return datetime.now(UTC)


_DUMMY_PASSWORD_HASH = hash_password("nonexistent-user-timing-equalization")


async def create_session(
    db: AsyncSession, user: User, settings: Settings, ip: str | None, user_agent: str | None
) -> str:
    raw_token = new_secret()
    now = now_utc()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=token_digest(raw_token),
            expires_at=now + timedelta(days=settings.auth_session_ttl_days),
            last_seen_at=now,
            ip_address=ip,
            user_agent=(user_agent or "")[:1000] or None,
        )
    )
    return raw_token


async def get_user_for_token(
    db: AsyncSession, raw_token: str | None
) -> tuple[User, AuthSession] | None:
    if not raw_token:
        return None
    result = await db.execute(
        select(User, AuthSession)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(AuthSession.token_hash == token_digest(raw_token))
    )
    pair = result.one_or_none()
    if pair is None:
        return None
    user, auth_session = pair
    now = now_utc()
    if auth_session.revoked_at is not None or auth_session.expires_at <= now:
        return None
    if user.status != UserStatus.ACTIVE:
        return None
    if auth_session.last_seen_at < now - timedelta(minutes=10):
        auth_session.last_seen_at = now
        await db.commit()
    return user, auth_session


async def revoke_session(db: AsyncSession, raw_token: str | None) -> None:
    if raw_token:
        result = await db.execute(
            select(AuthSession).where(AuthSession.token_hash == token_digest(raw_token))
        )
        item = result.scalar_one_or_none()
        if item is not None and item.revoked_at is None:
            item.revoked_at = now_utc()
            await db.commit()


async def revoke_all_sessions(db: AsyncSession, user_id: int) -> None:
    from sqlalchemy import update

    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await db.commit()


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    from sqlalchemy import delete

    now = now_utc()
    result = await db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
    await db.execute(
        delete(PendingRegistration).where(
            (PendingRegistration.expires_at <= now) | PendingRegistration.consumed_at.is_not(None)
        )
    )
    await db.execute(
        delete(PendingLink).where(
            (PendingLink.expires_at <= now) | PendingLink.consumed_at.is_not(None)
        )
    )
    await db.execute(
        delete(OAuthState).where(
            (OAuthState.expires_at <= now) | OAuthState.consumed_at.is_not(None)
        )
    )
    await db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def user_response_data(db: AsyncSession, user: User) -> dict[str, object]:
    linked = await db.scalar(
        select(Identity.id)
        .where(Identity.user_id == user.id, Identity.provider == IdentityProvider.GOOGLE)
        .limit(1)
    )
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "role": "admin" if user.role == UserRole.ADMIN else "user",
        "google_connected": linked is not None,
        "created_at": user.created_at,
    }


async def register(
    db: AsyncSession,
    email: str,
    nickname: str,
    password: str,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, str]:
    try:
        email_normalized = normalize_email(email)
        nickname_normalized = validate_nickname(nickname)
        validate_password(password)
    except ValidationError as exc:
        raise AuthError(exc.code, str(exc)) from exc
    if await db.scalar(select(User.id).where(User.email_normalized == email_normalized)):
        raise AuthError("EMAIL_ALREADY_EXISTS", "An account with this email already exists.", 409)
    if await db.scalar(select(User.id).where(User.nickname_normalized == nickname_normalized)):
        raise AuthError("NICKNAME_ALREADY_EXISTS", "This nickname is already in use.", 409)
    user = User(
        email=email.strip(),
        email_normalized=email_normalized,
        nickname=nickname.strip(),
        nickname_normalized=nickname_normalized,
        password_hash=hash_password(password),
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    try:
        await db.flush()
        token = await create_session(db, user, settings, ip, user_agent)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthError("ACCOUNT_CONFLICT", "Email or nickname is already in use.", 409) from exc
    logger.info("registration.succeeded user_id=%s", user.id)
    return user, token


async def login(
    db: AsyncSession,
    email: str,
    password: str,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
) -> tuple[User, str]:
    try:
        normalized = normalize_email(email)
    except ValidationError:
        normalized = email.strip().casefold()
    user = await db.scalar(select(User).where(User.email_normalized == normalized))
    # A fixed valid Argon2 hash keeps unknown email and bad password responses similar.
    valid, updated_hash = (
        verify_password_and_update(password, user.password_hash)
        if user is not None
        else verify_password_and_update(password, _DUMMY_PASSWORD_HASH)
    )
    if not valid:
        logger.warning("login.failed reason=invalid_credentials")
        raise AuthError("INVALID_CREDENTIALS", "Invalid email or password.", 401)
    assert user is not None
    if user.status != UserStatus.ACTIVE:
        logger.warning("login.failed user_id=%s reason=disabled", user.id)
        raise AuthError("ACCOUNT_DISABLED", "This account is disabled.", 403)
    if updated_hash is not None:
        user.password_hash = updated_hash
    user.last_login_at = now_utc()
    token = await create_session(db, user, settings, ip, user_agent)
    await db.commit()
    logger.info("login.succeeded user_id=%s", user.id)
    return user, token


def safe_return_to(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(char) < 32 for char in value):
        return "/"
    return value


async def get_identity(db: AsyncSession, provider_subject: str) -> Identity | None:
    return await db.scalar(
        select(Identity).where(
            Identity.provider == IdentityProvider.GOOGLE,
            Identity.provider_subject == provider_subject,
        )
    )


async def insert_google_identity(
    db: AsyncSession,
    user_id: int,
    subject: str,
    email: str,
    metadata: dict[str, object] | None = None,
) -> Identity:
    identity = Identity(
        user_id=user_id,
        provider=IdentityProvider.GOOGLE,
        provider_subject=subject,
        provider_email=email,
        metadata_json=metadata or {},
    )
    db.add(identity)
    await db.flush()
    return identity


async def update_nickname(db: AsyncSession, user: User, nickname: str) -> None:
    try:
        normalized = validate_nickname(nickname)
    except ValidationError as exc:
        raise AuthError(exc.code, str(exc)) from exc
    existing = await db.scalar(
        select(User.id).where(User.nickname_normalized == normalized, User.id != user.id)
    )
    if existing:
        raise AuthError("NICKNAME_ALREADY_EXISTS", "This nickname is already in use.", 409)
    user.nickname = nickname.strip()
    user.nickname_normalized = normalized
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthError("NICKNAME_ALREADY_EXISTS", "This nickname is already in use.", 409) from exc
