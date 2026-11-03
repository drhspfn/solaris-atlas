"""Hooks for account invariants that a generic ORM form cannot know."""

from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette_admin.exceptions import FormValidationError

from wuwa_story.auth.constants import UserRole, UserStatus
from wuwa_story.auth.security import hash_password
from wuwa_story.auth.services import now_utc
from wuwa_story.auth.validation import normalize_email, validate_nickname, validate_password
from wuwa_story.db.models.agent_workspace import AgentToolAudit
from wuwa_story.db.models.auth import AuthSession, User


def record(db: AsyncSession, request: Request, operation: str, identity: Any) -> None:
    db.add(AgentToolAudit(request_id=uuid4().hex, role="admin", call_id=uuid4().hex,
                          tool="console." + operation, status="completed", duration_ms=0,
                          arguments={"actor_user_id": request.state.console_user.id, "object_id": identity}, result={"saved": True}))


async def prepare_user(request: Request, data: dict[str, Any], obj: Any, *, creating: bool) -> None:
    db = request.state.session
    with db.no_autoflush:
        await db.execute(text("SELECT pg_advisory_xact_lock(706,1)"))
        old = None if creating else (await db.execute(select(User.role, User.status, User.email_normalized)
                                                      .where(User.id == obj.id).with_for_update())).one()
        role, status = UserRole(
            int(data["role"])), UserStatus(int(data["status"]))
        if old and old.role == UserRole.ADMIN and old.status == UserStatus.ACTIVE and (role != old.role or status != old.status):
            others = await db.scalar(select(func.count()).select_from(User).where(
                User.role == UserRole.ADMIN, User.status == UserStatus.ACTIVE, User.id != obj.id))
            if not others:
                raise FormValidationError(
                    {"role": "Keep at least one active administrator."})
    email = normalize_email(data["email"])
    validate_nickname(data["nickname"])
    password = data.get("password") or ""
    if creating or password:
        validate_password(password)
        obj.password_hash = hash_password(password)
    obj.email_normalized, obj.nickname_normalized = email, data["nickname"].casefold(
    )
    obj.role, obj.status = role, status
    if old and (password or role != old.role or status != old.status or email != old.email_normalized):
        with db.no_autoflush:
            await db.execute(update(AuthSession).where(AuthSession.user_id == obj.id,
                                                       AuthSession.revoked_at.is_(None)).values(revoked_at=now_utc()))
    await db.flush()
    record(db, request, "user.create" if creating else "user.edit", obj.id)
