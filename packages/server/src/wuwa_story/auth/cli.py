"""Administrative account maintenance commands."""

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wuwa_story.auth.constants import UserRole
from wuwa_story.auth.services import cleanup_expired_sessions
from wuwa_story.auth.validation import normalize_email
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.auth import User


async def _promote(email: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email_normalized == normalize_email(email))
            )
            if user is None:
                raise SystemExit("No account matches that email.")
            user.role = UserRole.ADMIN
            await session.commit()
            print(f"Promoted {user.email} to admin.")
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
    promote = commands.add_parser("promote", help="Promote a registered user to administrator")
    promote.add_argument("email")
    commands.add_parser("cleanup-sessions", help="Delete expired server-side sessions")
    args = parser.parse_args()
    if args.command == "promote":
        asyncio.run(_promote(args.email))
    else:
        asyncio.run(_cleanup())
