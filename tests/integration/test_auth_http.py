import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from wuwa_story.api.app import app
from wuwa_story.auth.models import AuthSession, User
from wuwa_story.auth.security import token_digest
from wuwa_story.db.session import get_session

DATABASE_URL = os.getenv("WUWA_TEST_DATABASE_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not DATABASE_URL, reason="WUWA_TEST_DATABASE_URL is not configured")
async def test_register_login_me_logout_and_csrf() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    email = f"auth-{uuid4().hex}@example.com"
    password = "long enough password 9"

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            csrf_result = client.get("/auth/csrf")
            assert csrf_result.status_code == 200
            csrf = csrf_result.json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}

            rejected = client.post(
                "/auth/register",
                json={
                    "email": email,
                    "nickname": "AtlasUser",
                    "password": password,
                },
            )
            assert rejected.status_code == 403

            registered = client.post(
                "/auth/register",
                headers=headers,
                json={
                    "email": email,
                    "nickname": "AtlasUser",
                    "password": password,
                    "role": "admin",
                },
            )
            assert registered.status_code == 200, registered.text
            payload = registered.json()
            assert payload["email"] == email
            assert payload["role"] == "user"
            assert "password" not in payload and "password_hash" not in payload
            raw_session = client.cookies.get("solaris_session")
            assert raw_session
            assert raw_session not in registered.text
            async with factory() as session:
                persisted_hash = await session.scalar(
                    select(AuthSession.token_hash).where(
                        AuthSession.user_id == payload["id"],
                        AuthSession.revoked_at.is_(None),
                    )
                )
            assert persisted_hash == token_digest(raw_session)
            assert any(
                cookie.startswith("solaris_session=") and "httponly" in cookie.lower()
                for cookie in registered.headers.get_list("set-cookie")
            )

            current = client.get("/auth/me")
            assert current.status_code == 200
            assert current.json()["nickname"] == "AtlasUser"

            # The email uniqueness check responds consistently, even if case changes.
            duplicate = client.post(
                "/auth/register",
                headers=headers,
                json={
                    "email": email.upper(),
                    "nickname": "AtlasUser2",
                    "password": password,
                },
            )
            assert duplicate.status_code == 409

            client.post("/auth/logout", headers=headers)
            assert client.get("/auth/me").status_code == 401

            login_csrf = client.get("/auth/csrf").json()["csrf_token"]
            logged_in = client.post(
                "/auth/login",
                headers={"X-CSRF-Token": login_csrf},
                json={
                    "email": email.upper(),
                    "password": password,
                },
            )
            assert logged_in.status_code == 200
            assert logged_in.json()["role"] == "user"
            wrong = client.post(
                "/auth/login",
                headers={"X-CSRF-Token": login_csrf},
                json={
                    "email": email,
                    "password": "incorrect password",
                },
            )
            unknown = client.post(
                "/auth/login",
                headers={"X-CSRF-Token": login_csrf},
                json={
                    "email": f"missing-{email}",
                    "password": "incorrect password",
                },
            )
            assert wrong.status_code == unknown.status_code == 401
            assert wrong.json() == unknown.json()
    finally:
        app.dependency_overrides.clear()
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email_normalized == email.casefold())
            )
            if user:
                await session.delete(user)
                await session.commit()
        await engine.dispose()
