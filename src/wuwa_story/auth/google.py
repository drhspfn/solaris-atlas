import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from wuwa_story.config.settings import Settings

DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"


class GoogleOAuthError(Exception):
    pass


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    import base64

    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class GoogleOAuthService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=15)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        if (
            not self.settings.google_client_id
            or not self.settings.google_client_secret.get_secret_value()
        ):
            raise GoogleOAuthError("Google OAuth is not configured.")
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    async def exchange_and_validate(self, code: str, verifier: str, nonce: str) -> dict[str, Any]:
        try:
            discovery_response = await self.client.get(DISCOVERY_URL)
            discovery_response.raise_for_status()
            discovery = discovery_response.json()
            token_response = await self.client.post(
                discovery["token_endpoint"],
                data={
                    "code": code,
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret.get_secret_value(),
                    "redirect_uri": self.settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            id_token = token_response.json()["id_token"]
            jwks_response = await self.client.get(discovery["jwks_uri"])
            jwks_response.raise_for_status()
            key_set = KeySet.import_key_set(jwks_response.json())
            decoded = jwt.decode(id_token, key_set, algorithms={"RS256"})
            claims = decoded.claims
            now = datetime.now(UTC).timestamp()
            issuer = claims.get("iss")
            audience = claims.get("aud")
            if decoded.header.get("alg") != "RS256":
                raise GoogleOAuthError("Unexpected ID token algorithm.")
            if issuer not in {"https://accounts.google.com", "accounts.google.com"}:
                raise GoogleOAuthError("Invalid ID token issuer.")
            if self.settings.google_client_id not in (
                audience if isinstance(audience, list) else [audience]
            ):
                raise GoogleOAuthError("Invalid ID token audience.")
            if isinstance(audience, list) and claims.get("azp") != self.settings.google_client_id:
                raise GoogleOAuthError("Invalid authorized party.")
            if float(claims.get("exp", 0)) <= now or float(claims.get("iat", 0)) > now + 60:
                raise GoogleOAuthError("Expired or premature ID token.")
            if claims.get("nonce") != nonce:
                raise GoogleOAuthError("Invalid ID token nonce.")
            if (
                not claims.get("sub")
                or not claims.get("email")
                or claims.get("email_verified") is not True
            ):
                raise GoogleOAuthError("Google account has no verified email.")
            return claims
        except (httpx.HTTPError, KeyError, ValueError, TypeError, JoseError) as exc:
            raise GoogleOAuthError("Google authentication could not be verified.") from exc
