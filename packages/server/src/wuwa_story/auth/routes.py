import logging
from datetime import timedelta
from ipaddress import ip_address

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.auth.constants import IdentityProvider, UserRole, UserStatus
from wuwa_story.auth.dependencies import (
    AuthContext,
    get_auth_context,
    optional_auth_context,
    require_csrf,
)
from wuwa_story.auth.google import GoogleOAuthError, GoogleOAuthService
from wuwa_story.auth.schemas import (
    CompleteGoogleRequest,
    LinkGoogleRequest,
    LoginRequest,
    MessageResponse,
    NicknameRequest,
    PendingGoogleResponse,
    RegisterRequest,
    UserResponse,
)
from wuwa_story.auth.security import (
    hash_password,
    new_secret,
    token_digest,
    verify_password_and_update,
)
from wuwa_story.auth.services import (
    AuthError,
    create_session,
    get_identity,
    get_user_for_token,
    insert_google_identity,
    login,
    now_utc,
    register,
    safe_return_to,
    update_nickname,
    user_response_data,
)
from wuwa_story.auth.validation import (
    normalize_email,
    suggested_nickname,
    validate_nickname,
    validate_password,
)
from wuwa_story.config.settings import Settings, get_settings
from wuwa_story.db.models.auth import Identity, OAuthState, PendingLink, PendingRegistration, User
from wuwa_story.db.session import get_session

router = APIRouter(tags=["authentication"])
logger = logging.getLogger("wuwa_story.auth")
account_router = APIRouter(prefix="/account", tags=["account"])
REGISTRATION_COOKIE = "solaris_registration"
PENDING_LINK_COOKIE = "solaris_pending_link"
OAUTH_STATE_TTL = timedelta(minutes=10)


def _cookie(
    response: Response,
    settings: Settings,
    name: str,
    value: str,
    *,
    http_only: bool,
    max_age: int | None = None,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=http_only,
        secure=settings.auth_cookie_is_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
        domain=settings.auth_cookie_domain,
    )


def _session_cookie(response: Response, token: str, settings: Settings) -> None:
    _cookie(
        response,
        settings,
        settings.auth_cookie_name,
        token,
        http_only=True,
        max_age=settings.auth_session_ttl_days * 86400,
    )


def _clear_cookie(response: Response, name: str, settings: Settings) -> None:
    response.delete_cookie(
        name,
        path="/",
        domain=settings.auth_cookie_domain,
        secure=settings.auth_cookie_is_secure,
        httponly=name != settings.auth_csrf_cookie_name,
        samesite=settings.auth_cookie_samesite,
    )


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    try:
        return str(ip_address(request.client.host))
    except ValueError:
        return None


@router.get("/auth/csrf")
async def csrf(response: Response, settings: Settings = Depends(get_settings)) -> dict[str, str]:
    from wuwa_story.auth.security import csrf_token

    value = csrf_token(settings.auth_csrf_secret.get_secret_value())
    _cookie(
        response, settings, settings.auth_csrf_cookie_name, value, http_only=False, max_age=86400
    )
    return {"csrf_token": value}


@router.post("/auth/register", response_model=UserResponse, dependencies=[Depends(require_csrf)])
async def register_route(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    user, token = await register(
        db,
        str(payload.email),
        payload.nickname,
        payload.password,
        settings,
        _client_ip(request),
        request.headers.get("user-agent"),
    )
    _session_cookie(response, token, settings)
    return await user_response_data(db, user)


@router.post("/auth/login", response_model=UserResponse, dependencies=[Depends(require_csrf)])
async def login_route(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    user, token = await login(
        db,
        str(payload.email),
        payload.password,
        settings,
        _client_ip(request),
        request.headers.get("user-agent"),
    )
    _session_cookie(response, token, settings)
    return await user_response_data(db, user)


@router.post("/auth/logout", response_model=MessageResponse, dependencies=[Depends(require_csrf)])
async def logout_route(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    from wuwa_story.auth.services import revoke_session

    await revoke_session(db, request.cookies.get(settings.auth_cookie_name))
    logger.info("logout.completed")
    _clear_cookie(response, settings.auth_cookie_name, settings)
    return {"message": "Signed out."}


@router.get("/auth/me", response_model=UserResponse)
async def me(
    context: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    return await user_response_data(db, context.user)


@account_router.get("", response_model=UserResponse)
async def account(
    context: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    return await user_response_data(db, context.user)


@account_router.patch("", response_model=UserResponse, dependencies=[Depends(require_csrf)])
async def patch_account(
    payload: NicknameRequest,
    context: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    await update_nickname(db, context.user, payload.nickname)
    return await user_response_data(db, context.user)


@router.get("/auth/google")
async def google_start(
    request: Request,
    mode: str = Query(default="login", pattern="^(login|link)$"),
    return_to: str | None = None,
    current: AuthContext | None = Depends(optional_auth_context),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if mode == "link" and current is None:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Sign in first."})
    state, nonce, verifier = new_secret(), new_secret(), new_secret()
    state_row = OAuthState(
        token_hash=token_digest(state),
        mode=mode,
        user_id=current.user.id if mode == "link" and current else None,
        session_id=current.session.id if mode == "link" and current else None,
        nonce=nonce,
        code_verifier=verifier,
        return_to=safe_return_to(return_to),
        expires_at=now_utc() + OAUTH_STATE_TTL,
    )
    db.add(state_row)
    await db.commit()
    oauth = GoogleOAuthService(settings)
    try:
        location = oauth.authorization_url(state, nonce, verifier)
    except GoogleOAuthError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "GOOGLE_NOT_CONFIGURED",
                "message": "Google sign-in is not configured.",
            },
        ) from exc
    finally:
        await oauth.close()
    return RedirectResponse(location, status_code=302)


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    callback_query = getattr(request.state, "oauth_query", {})
    states = callback_query.get("state", [])
    codes = callback_query.get("code", [])
    if not states or not codes or not states[0] or not codes[0]:
        raise HTTPException(
            400,
            detail={"code": "OAUTH_FAILED", "message": "Google sign-in could not be completed."},
        )
    state, code = states[0], codes[0]
    row = await db.scalar(select(OAuthState).where(OAuthState.token_hash == token_digest(state)))
    if row is None or row.expires_at <= now_utc() or row.consumed_at is not None:
        raise HTTPException(
            400,
            detail={
                "code": "OAUTH_STATE_INVALID",
                "message": "Google sign-in expired. Please try again.",
            },
        )
    consumed = await db.execute(
        update(OAuthState)
        .where(
            OAuthState.id == row.id,
            OAuthState.consumed_at.is_(None),
            OAuthState.expires_at > now_utc(),
        )
        .values(consumed_at=now_utc())
        .returning(OAuthState.id)
    )
    if consumed.scalar_one_or_none() is None:
        await db.rollback()
        raise HTTPException(
            400,
            detail={
                "code": "OAUTH_STATE_INVALID",
                "message": "Google sign-in expired. Please try again.",
            },
        )
    await db.commit()
    oauth = GoogleOAuthService(settings)
    try:
        claims = await oauth.exchange_and_validate(code, row.code_verifier, row.nonce)
    except GoogleOAuthError as exc:
        raise HTTPException(
            401, detail={"code": "OAUTH_FAILED", "message": "Google sign-in could not be verified."}
        ) from exc
    finally:
        await oauth.close()
    subject = str(claims["sub"])
    email = str(claims["email"])
    normalized = normalize_email(email)
    metadata: dict[str, object] = {
        key: str(claims[key]) for key in ("name", "picture", "locale") if claims.get(key)
    }
    identity = await get_identity(db, subject)

    if row.mode == "link":
        raw_session = request.cookies.get(settings.auth_cookie_name)
        auth_context = await get_user_for_token(db, raw_session)
        if (
            auth_context is None
            or auth_context[1].id != row.session_id
            or auth_context[0].id != row.user_id
        ):
            return RedirectResponse(
                settings.frontend_url.rstrip("/") + "/account?auth_error=session_expired",
                status_code=303,
            )
        if identity is not None and identity.user_id != row.user_id:
            return RedirectResponse(
                settings.frontend_url.rstrip("/") + "/account?auth_error=google_already_linked",
                status_code=303,
            )
        if identity is None:
            try:
                await insert_google_identity(
                    db, row.user_id or auth_context[0].id, subject, email, metadata
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return RedirectResponse(
                    settings.frontend_url.rstrip("/") + "/account?auth_error=google_already_linked",
                    status_code=303,
                )
        return RedirectResponse(
            settings.frontend_url.rstrip("/") + "/account?google=connected", status_code=303
        )

    if identity is not None:
        user = await db.get(User, identity.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            return RedirectResponse(
                settings.frontend_url.rstrip("/") + "/login?error=account_disabled", status_code=303
            )
        token = await create_session(
            db, user, settings, _client_ip(request), request.headers.get("user-agent")
        )
        user.last_login_at = now_utc()
        await db.commit()
        logger.info("google.login.succeeded user_id=%s", user.id)
        redirect = RedirectResponse(
            settings.frontend_url.rstrip("/") + row.return_to, status_code=303
        )
        _session_cookie(redirect, token, settings)
        return redirect

    existing_user = await db.scalar(select(User).where(User.email_normalized == normalized))
    if existing_user is not None:
        logger.info("google.local_email_requires_password_link user_id=%s", existing_user.id)
        pending = new_secret()
        db.add(
            PendingLink(
                token_hash=token_digest(pending),
                user_id=existing_user.id,
                provider=IdentityProvider.GOOGLE,
                provider_subject=subject,
                provider_email=email,
                expires_at=now_utc() + timedelta(minutes=settings.auth_pending_link_ttl_minutes),
            )
        )
        await db.commit()
        redirect = RedirectResponse(
            settings.frontend_url.rstrip("/") + "/auth/google/link-existing", status_code=303
        )
        _cookie(
            redirect,
            settings,
            PENDING_LINK_COOKIE,
            pending,
            http_only=True,
            max_age=settings.auth_pending_link_ttl_minutes * 60,
        )
        return redirect

    taken = set((await db.scalars(select(User.nickname_normalized))).all())
    pending = new_secret()
    db.add(
        PendingRegistration(
            token_hash=token_digest(pending),
            provider=IdentityProvider.GOOGLE,
            provider_subject=subject,
            email=email,
            email_normalized=normalized,
            suggested_nickname=suggested_nickname(email, taken),
            metadata_json=metadata,
            expires_at=now_utc()
            + timedelta(minutes=settings.auth_pending_registration_ttl_minutes),
        )
    )
    await db.commit()
    logger.info("google.registration_pending")
    redirect = RedirectResponse(
        settings.frontend_url.rstrip("/") + "/auth/google/complete", status_code=303
    )
    _cookie(
        redirect,
        settings,
        REGISTRATION_COOKIE,
        pending,
        http_only=True,
        max_age=settings.auth_pending_registration_ttl_minutes * 60,
    )
    return redirect


@router.get("/auth/google/pending", response_model=PendingGoogleResponse)
async def google_pending(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    raw = request.cookies.get(REGISTRATION_COOKIE)
    pending = await db.scalar(
        select(PendingRegistration).where(PendingRegistration.token_hash == token_digest(raw or ""))
    )
    if pending is None or pending.consumed_at is not None or pending.expires_at <= now_utc():
        raise HTTPException(
            401,
            detail={
                "code": "PENDING_REGISTRATION_EXPIRED",
                "message": "Google registration expired. Start again.",
            },
        )
    return {"email": pending.email, "nickname": pending.suggested_nickname}


@router.post(
    "/auth/google/complete", response_model=UserResponse, dependencies=[Depends(require_csrf)]
)
async def complete_google(
    payload: CompleteGoogleRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    raw = request.cookies.get(REGISTRATION_COOKIE)
    pending = await db.scalar(
        select(PendingRegistration).where(PendingRegistration.token_hash == token_digest(raw or ""))
    )
    if pending is None or pending.consumed_at is not None or pending.expires_at <= now_utc():
        raise HTTPException(
            401,
            detail={
                "code": "PENDING_REGISTRATION_EXPIRED",
                "message": "Google registration expired. Start again.",
            },
        )
    try:
        nickname_normalized = validate_nickname(payload.nickname)
        validate_password(payload.password)
    except ValueError as exc:
        raise AuthError(getattr(exc, "code", "INVALID_INPUT"), str(exc)) from exc
    if await db.scalar(select(User.id).where(User.nickname_normalized == nickname_normalized)):
        raise AuthError("NICKNAME_ALREADY_EXISTS", "This nickname is already in use.", 409)
    if await db.scalar(select(User.id).where(User.email_normalized == pending.email_normalized)):
        raise AuthError("EMAIL_ALREADY_EXISTS", "An account with this email already exists.", 409)
    user = User(
        email=pending.email,
        email_normalized=pending.email_normalized,
        nickname=payload.nickname.strip(),
        nickname_normalized=nickname_normalized,
        password_hash=hash_password(payload.password),
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    try:
        pending.consumed_at = now_utc()
        await db.flush()
        await insert_google_identity(
            db, user.id, pending.provider_subject, pending.email, pending.metadata_json
        )
        token = await create_session(
            db, user, settings, _client_ip(request), request.headers.get("user-agent")
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthError("ACCOUNT_CONFLICT", "Email or nickname is already in use.", 409) from exc
    _session_cookie(response, token, settings)
    _clear_cookie(response, REGISTRATION_COOKIE, settings)
    return await user_response_data(db, user)


@router.get("/auth/google/link-existing", response_model=PendingGoogleResponse)
async def pending_link_info(
    request: Request, db: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    raw = request.cookies.get(PENDING_LINK_COOKIE)
    pending = await db.scalar(
        select(PendingLink).where(PendingLink.token_hash == token_digest(raw or ""))
    )
    if pending is None or pending.consumed_at is not None or pending.expires_at <= now_utc():
        raise HTTPException(
            401,
            detail={
                "code": "PENDING_LINK_EXPIRED",
                "message": "Google connection expired. Start again.",
            },
        )
    user = await db.get(User, pending.user_id)
    return {"email": pending.provider_email, "nickname": user.nickname if user else ""}


@router.post(
    "/auth/google/link-existing", response_model=UserResponse, dependencies=[Depends(require_csrf)]
)
async def link_existing_google(
    payload: LinkGoogleRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    raw = request.cookies.get(PENDING_LINK_COOKIE)
    pending = await db.scalar(
        select(PendingLink).where(PendingLink.token_hash == token_digest(raw or ""))
    )
    if pending is None or pending.consumed_at is not None or pending.expires_at <= now_utc():
        raise HTTPException(
            401,
            detail={
                "code": "PENDING_LINK_EXPIRED",
                "message": "Google connection expired. Start again.",
            },
        )
    user = await db.get(User, pending.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise AuthError("INVALID_CREDENTIALS", "Invalid email or password.", 401)
    valid_password, updated_hash = verify_password_and_update(payload.password, user.password_hash)
    if not valid_password:
        raise AuthError("INVALID_CREDENTIALS", "Invalid email or password.", 401)
    if await get_identity(db, pending.provider_subject):
        raise AuthError(
            "GOOGLE_IDENTITY_ALREADY_LINKED", "This Google account is already connected.", 409
        )
    pending.consumed_at = now_utc()
    if updated_hash is not None:
        user.password_hash = updated_hash
    try:
        await insert_google_identity(db, user.id, pending.provider_subject, pending.provider_email)
        token = await create_session(
            db, user, settings, _client_ip(request), request.headers.get("user-agent")
        )
        user.last_login_at = now_utc()
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AuthError(
            "GOOGLE_IDENTITY_ALREADY_LINKED", "This Google account is already connected.", 409
        ) from exc
    _session_cookie(response, token, settings)
    _clear_cookie(response, PENDING_LINK_COOKIE, settings)
    return await user_response_data(db, user)


@router.delete(
    "/auth/identities/google", response_model=MessageResponse, dependencies=[Depends(require_csrf)]
)
async def unlink_google(
    context: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    identity = await db.scalar(
        select(Identity).where(
            Identity.user_id == context.user.id, Identity.provider == IdentityProvider.GOOGLE
        )
    )
    if identity is None:
        raise AuthError("GOOGLE_NOT_CONNECTED", "No Google account is connected.", 404)
    await db.delete(identity)
    await db.commit()
    logger.info("google.identity_disconnected user_id=%s", context.user.id)
    return {"message": "Google account disconnected."}


router.include_router(account_router)
