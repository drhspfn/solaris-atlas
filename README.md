# WuWa Story Platform

Python 3.12 source-traceable Wuthering Waves story platform. It provides a PostgreSQL 17 schema, immutable compiler-source ingestion, canonical graph storage, localization, deterministic lexical search, a file-storage abstraction, and a small FastAPI surface. Semantic extraction remains out of scope; imported localized text is indexed deterministically for lexical search.

## Quick start

Requirements: Docker with Compose, `uv`, and Python 3.12 (or let `uv` install the pinned interpreter).

```bash
cp .env.example .env
uv sync --python 3.12
docker compose up -d postgres minio minio-init
uv run alembic upgrade head
uv run uvicorn wuwa_story.api.app:app --reload
```

Open `http://localhost:8000/docs`; `/health` checks process health and `/health/db` checks PostgreSQL.
The frontend API link opens this backend Swagger directly; set `VITE_API_DOCS_URL` when deploying the web app somewhere other than a local host.


## Authentication

Browser authentication uses server-side opaque sessions in HttpOnly cookies, Argon2id password hashes, and CSRF protection. Register and sign in at `/register` and `/login`; Google login/link uses the server-side OpenID Connect flow documented in [docs/google-oauth.md](docs/google-oauth.md). Configure the auth and Google placeholders in `.env.example`, then apply migrations with `uv run alembic upgrade head`.

Promote an existing user to administrator with `uv run wuwa-story-admin promote user@example.com`. Remove expired sessions with `uv run wuwa-story-admin cleanup-sessions`. See [docs/AUTH_IMPLEMENTATION_REPORT.md](docs/AUTH_IMPLEMENTATION_REPORT.md) for routes and behavior.

## Import a compiler dataset

The importer consumes the deterministic filesystem output from the WuWa narrative compiler, including `manifest.json`, `coverage.json`, `raw-evidence/`, `entities/`, and `graphs/global.jsonl`. It verifies indexed raw file hashes and byte sizes, retains raw records and edge provenance, imports all locale values including explicit empty strings, builds a lexical search index for the release, and never generates semantic claims.

```bash
uv run wuwa-story-import /path/to/compiled-dataset
```

See [docs/ingestion.md](docs/ingestion.md) for the current programmatic entrypoint and invariants.

## Development

```bash
uv run pytest
uv run ruff check .
docker compose config
```

## Frontend

The first browsing interface is a React + Vite SPA in `apps/web`. It includes the home page, searchable character/item/location/quest catalogs, entity profiles, relationship browsing, and quest transcripts. The frontend uses the FastAPI endpoints through `/api`; Vite proxies this path in development and the Compose Nginx container proxies it in production.

```bash
cd apps/web
npm ci
npm run dev
```

For the full Compose stack, build and open `http://localhost:3000`:

```bash
docker compose up -d --build web
```

PostgreSQL integration checks require the local Compose database. See [docs/database.md](docs/database.md) and [docs/storage.md](docs/storage.md). Set `WUWA_TEST_DATABASE_URL` to enable optional integration tests.

## Architecture and status

- [Story browsing API](docs/story-browsing.md)
- [Frontend API contract — iteration 1](docs/frontend-api-v1.md)
- [Architecture](docs/architecture.md)
- [Database schema](docs/database.md)
- [Storage contract](docs/storage.md)
- [Ingestion contract](docs/ingestion.md)
- [Foundation report](docs/FOUNDATION_REPORT.md)

This project does not include upstream repositories or compiled game datasets. Configure a local compiled dataset path when running an import.
