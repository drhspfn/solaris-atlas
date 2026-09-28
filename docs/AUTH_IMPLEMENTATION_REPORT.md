# Authentication implementation report

## Implemented

- Added the PostgreSQL `auth` schema with `user`, `session`, `identity`, `pending_registration`, `pending_link`, and `oauth_state` tables in migration `0004_auth`.
- Added email/nickname/password registration, login, logout, current-user, account nickname update, Google identity link/unlink, session cleanup, and user promotion services.
- Sessions use 384-bit random opaque cookie values; only SHA-256 digests are stored. Passwords use Argon2id. Responses never serialize SQLAlchemy models or credential fields.
- Cookies are HttpOnly for session and pending credentials, SameSite is configurable, Secure is enabled by default in production, and cookie domain is host-only unless configured.
- State-changing API methods require a signed double-submit CSRF cookie/header. CORS uses explicit configured origins with credentials.
- Auth success/failure logs contain event codes or internal user IDs only. OAuth callback query strings are removed from the ASGI scope before Uvicorn access logging, so one-time `code` and `state` values are not logged by the app.
- Google uses OIDC authorization code, one-time expiring server-side state, PKCE S256, nonce, JWKS signature verification, issuer/audience/expiry checks, and `email_verified`. Provider access and refresh tokens are not stored.
- A new Google identity goes through pending registration; a matching local email requires the local password before linking. Linking while signed in is tied to the originating session. Google unlink is allowed because all accounts have a local password.
- Added `/login`, `/register`, `/auth/google/complete`, `/auth/google/link-existing`, and `/account` pages using Solaris Atlas styling.

## Routes

- `GET /auth/csrf`, `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- `GET /auth/google`, `GET /auth/google/callback`, `GET /auth/google/pending`, `POST /auth/google/complete`
- `GET /auth/google/link-existing`, `POST /auth/google/link-existing`, `DELETE /auth/identities/google`
- `GET /account`, `PATCH /account`

## Admin and session maintenance

- `wuwa-story-admin promote <email>` promotes an existing user.
- `wuwa-story-admin cleanup-sessions` removes expired sessions.

## Verification

- `uv run alembic upgrade head` — passed. Local project database is at revision `0004_auth`; all six auth tables are present.
- `WUWA_TEST_DATABASE_URL=... uv run pytest` — **27 passed**. The DB-backed HTTP test covers registration, case-insensitive duplicate email, user-only role assignment, HttpOnly opaque session cookie, DB token digest, `/auth/me`, logout, CSRF rejection, login, and indistinguishable wrong/unknown email errors. Google OIDC signature and claims validation is exercised using an in-process HTTP mock; no Google request is made.
- `uv run ruff check .` — passed.
- `uv run mypy src/wuwa_story/auth src/wuwa_story/api/app.py src/wuwa_story/config/settings.py` — passed (13 source files).
- `uv run mypy src` — still reports **65 errors in 8 existing non-auth modules** (`storage/s3.py`, `ingestion/raw.py`, `api/routes/story.py`, `search/indexer.py`, `ingestion/canonical_import.py`, `api/routes/nodes.py`, `ingestion/compiler_importer.py`, and `workers/cli.py`). No auth module is among those errors.
- `cd apps/web && npm run build` — passed (TypeScript check and Vite production build).
- The test client emits one Starlette deprecation warning about using HTTPX; tests pass.

## Google Cloud setup

See [google-oauth.md](google-oauth.md). Remaining setup is to create the Google OAuth web client, register the exact redirect URI, set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, configure public frontend/API URLs and a production CSRF secret, then restart the API.

## Known limits

Rate limiting is left at the reverse-proxy boundary for now; the auth routes remain independently addressable for a later limiter. Full Google browser redirect/callback was not exercised against Google, because credentials are intentionally absent; cryptographic ID-token validation and the normal account flows are covered locally. Full-repository mypy is not clean due to the listed existing modules. Email verification, password recovery, 2FA, password changes, account deletion, and admin UI remain out of scope. No Google credentials are present in this repository.
