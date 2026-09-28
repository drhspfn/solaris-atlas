from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.constants import UserRole
from wuwa_story.auth.models import AuthSession, User
from wuwa_story.auth.security import validate_csrf_token
from wuwa_story.auth.services import get_user_for_token
from wuwa_story.config.settings import get_settings
from wuwa_story.db.session import get_session


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


async def get_auth_context(
    token: str | None = Cookie(default=None, alias=get_settings().auth_cookie_name),
    db: AsyncSession = Depends(get_session),
) -> AuthContext:
    context = await get_user_for_token(db, token)
    if context is None:
        raise HTTPException(
            status_code=401, detail={"code": "UNAUTHENTICATED", "message": "Sign in to continue."}
        )
    return AuthContext(*context)


async def get_current_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user


async def require_authenticated_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user


async def require_admin(context: AuthContext = Depends(get_auth_context)) -> User:
    if context.user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Administrator access required."},
        )
    return context.user


async def optional_auth_context(
    token: str | None = Cookie(default=None, alias=get_settings().auth_cookie_name),
    db: AsyncSession = Depends(get_session),
) -> AuthContext | None:
    context = await get_user_for_token(db, token)
    return AuthContext(*context) if context is not None else None


async def require_csrf(
    request: Request,
    csrf_cookie: str | None = Cookie(default=None, alias=get_settings().auth_csrf_cookie_name),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    settings = get_settings()
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(
            status_code=403,
            detail={"code": "CSRF_INVALID", "message": "Request verification failed."},
        )
    if not validate_csrf_token(csrf_cookie, settings.auth_csrf_secret.get_secret_value()):
        raise HTTPException(
            status_code=403,
            detail={"code": "CSRF_INVALID", "message": "Request verification failed."},
        )
