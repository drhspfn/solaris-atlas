from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import SecretStr

from wuwa_story.auth.google import GoogleOAuthService, pkce_challenge
from wuwa_story.auth.security import (
    csrf_token,
    hash_password,
    token_digest,
    validate_csrf_token,
    verify_password,
)
from wuwa_story.auth.services import safe_return_to
from wuwa_story.auth.validation import (
    ValidationError,
    normalize_email,
    suggested_nickname,
    validate_nickname,
    validate_password,
)
from wuwa_story.config.settings import Settings


def test_email_normalization_is_case_insensitive_without_provider_specific_rewriting() -> None:
    assert normalize_email("  User+Tag@Example.com ") == "user+tag@example.com"


def test_nickname_rules_and_casefolding() -> None:
    assert validate_nickname("Denia_7") == "denia_7"
    for value in ("ab", " withspace", "with space", "Жан", "name-tag", "admin"):
        try:
            validate_nickname(value)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"nickname unexpectedly accepted: {value}")


def test_password_bounds_allow_unicode() -> None:
    validate_password("пароль🙂12")
    for value in ("short7!", "x" * 129):
        try:
            validate_password(value)
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid password length accepted")


def test_argon2_hash_and_session_digest() -> None:
    hashed = hash_password("another correct horse")
    assert hashed.startswith("$argon2id$")
    assert verify_password("another correct horse", hashed)
    assert not verify_password("wrong", hashed)
    assert token_digest("opaque") != b"opaque"


def test_csrf_double_submit_signature() -> None:
    token = csrf_token("local secret")
    assert validate_csrf_token(token, "local secret")
    assert not validate_csrf_token(token, "different secret")
    assert not validate_csrf_token("invalid", "local secret")


def test_google_pkce_and_open_redirect_guard() -> None:
    assert len(pkce_challenge("random-verifier")) == 43
    assert safe_return_to("/story/quest-1") == "/story/quest-1"
    assert safe_return_to("//attacker.example") == "/"
    assert safe_return_to("https://attacker.example") == "/"
    assert safe_return_to("/\\attacker.example") == "/"


def test_google_nickname_suggestion_is_valid_and_unique() -> None:
    value = suggested_nickname("example.user@gmail.com", {"example.user"})
    assert value == "example.user1"
    assert validate_nickname(value) == "example.user1"


def test_google_authorization_uses_minimal_scopes_nonce_state_and_pkce() -> None:
    settings = Settings(
        google_client_id="public-client-id",
        google_client_secret=SecretStr("server-secret"),
        google_redirect_uri="https://api.example.com/auth/google/callback",
    )
    service = GoogleOAuthService(settings)
    try:
        query = parse_qs(
            urlparse(service.authorization_url("one-time-state", "nonce", "verifier")).query
        )
    finally:
        import asyncio

        asyncio.run(service.close())
    assert query["scope"] == ["openid email profile"]
    assert query["state"] == ["one-time-state"]
    assert query["nonce"] == ["nonce"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [pkce_challenge("verifier")]


@pytest.mark.asyncio
async def test_google_id_token_is_verified_without_calling_google() -> None:
    key = RSAKey.generate_key(2048, {"kid": "test-key"}, private=True)
    now = datetime.now(UTC)
    token = jwt.encode(
        {"alg": "RS256", "kid": "test-key"},
        {
            "iss": "https://accounts.google.com",
            "aud": "test-client",
            "sub": "google-subject",
            "email": "person@example.com",
            "email_verified": True,
            "nonce": "test-nonce",
            "iat": now.timestamp(),
            "exp": (now + timedelta(minutes=5)).timestamp(),
        },
        key,
        algorithms={"RS256"},
    )

    def provider(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "token_endpoint": "https://oauth.test/token",
                    "jwks_uri": "https://oauth.test/keys",
                },
            )
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200, json={"id_token": token, "access_token": "must-not-be-retained"}
            )
        return httpx.Response(200, json={"keys": [key.as_dict(private=False)]})

    settings = Settings(
        google_client_id="test-client",
        google_client_secret=SecretStr("server-only-secret"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        claims = await GoogleOAuthService(settings, client).exchange_and_validate(
            "authorization-code", "pkce-verifier", "test-nonce"
        )
    assert claims["sub"] == "google-subject"
    assert claims["email_verified"] is True
