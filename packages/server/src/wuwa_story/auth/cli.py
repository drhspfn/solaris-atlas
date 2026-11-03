"""Administrative account maintenance commands."""

import argparse
import asyncio
import getpass
import os

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.auth.constants import UserRole
from wuwa_story.auth.services import AuthError, cleanup_expired_sessions, register
from wuwa_story.auth.validation import normalize_email
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.auth import AuthSession, User


async def _promote(email: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(
                select(User).where(
                    User.email_normalized == normalize_email(email))
            )
            if user is None:
                raise SystemExit("No account matches that email.")
            user.role = UserRole.ADMIN
            await session.commit()
            print(f"Promoted {user.email} to admin.")
    finally:
        await engine.dispose()


async def _create_admin(email: str, nickname: str, password: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            user, _ = await register(db, email, nickname, password, settings, None, "admin-cli")
            user.role = UserRole.ADMIN
            # CLI provisioning does not issue a browser login.
            await db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            await db.commit()
            print(f"Created administrator {user.email}.")
    finally:
        await engine.dispose()


async def _cleanup() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            count = await cleanup_expired_sessions(session)
            print(f"Removed {count} expired sessions.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="wuwa-story-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    promote = commands.add_parser(
        "promote", help="Promote a registered user to administrator")
    promote.add_argument("email")
    create = commands.add_parser(
        "create-admin", help="Create an internal administrator")
    create.add_argument("email")
    create.add_argument("nickname")
    create.add_argument(
        "--password-env", help="Read the password from this environment variable instead of prompting")
    commands.add_parser("cleanup-sessions",
                        help="Delete expired server-side sessions")
    args = parser.parse_args()
    if args.command == "promote":
        asyncio.run(_promote(args.email))
    elif args.command == "create-admin":
        password = os.environ.get(
            args.password_env, "") if args.password_env else getpass.getpass("Password: ")
        if not password:
            raise SystemExit("Password is required.")
        if not args.password_env and password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords do not match.")
        try:
            asyncio.run(_create_admin(args.email, args.nickname, password))
        except AuthError as error:
            raise SystemExit(error.message) from None
    else:
        asyncio.run(_cleanup())
