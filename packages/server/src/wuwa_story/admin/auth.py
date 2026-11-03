"""Adapt the library's login to Solaris's existing opaque DB sessions."""

from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette_admin.auth import AdminUser, AuthProvider
from starlette_admin.exceptions import LoginFailed

from wuwa_story.auth.constants import UserRole
from wuwa_story.auth.routes import _clear_cookie, _session_cookie
from wuwa_story.auth.services import AuthError, get_user_for_token, login, revoke_session
from wuwa_story.config.settings import Settings


class ConsoleProtection(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Protect the generated forms/AJAX without forking the library templates.
        action = request.url.path.endswith(
            ("/action", "/row-action", "/logout"))
        if request.method not in {"GET", "HEAD", "OPTIONS"} or action:
            source = request.headers.get(
                "origin") or request.headers.get("referer", "")
            parsed = urlsplit(source)
            if (parsed.scheme, parsed.netloc) != (request.url.scheme, request.url.netloc):
                return PlainTextResponse("Request verification failed. Open the console and retry.", 403)
        try:
            response = await call_next(request)
        except HTTPException as error:
            response = PlainTextResponse(
                str(error.detail), status_code=error.status_code)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class InternalAuth(AuthProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    async def is_authenticated(self, request: Request) -> bool:
        context = await get_user_for_token(request.state.session,
                                           request.cookies.get(self.settings.auth_cookie_name))
        if context is None:
            return False
        user, session = context
        if user.role != UserRole.ADMIN:
            raise HTTPException(403, "Administrator access required.")
        request.state.console_user = user
        request.state.console_session_id = session.id
        return True

    def get_admin_user(self, request: Request) -> AdminUser | None:
        user = getattr(request.state, "console_user", None)
        return AdminUser(username=user.nickname) if user else None

    async def login(self, username: str, password: str, remember_me: bool, request: Request, response: Response) -> Response:
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 320 or not 1 <= len(password) <= 128:
            raise LoginFailed("Invalid administrator credentials.")
        try:
            _, token = await login(request.state.session, username, password, self.settings,
                                   None, request.headers.get("user-agent"), required_role=UserRole.ADMIN)
        except AuthError:
            raise LoginFailed("Invalid administrator credentials.") from None
        _session_cookie(response, token, self.settings)
        return response

    async def logout(self, request: Request, response: Response) -> Response:
        await revoke_session(request.state.session, request.cookies.get(self.settings.auth_cookie_name))
        _clear_cookie(response, self.settings.auth_cookie_name, self.settings)
        return response
