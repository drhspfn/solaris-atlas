# WuWa Story Platform

Source-traceable Wuthering Waves story archive and browsing API.

## Repository layout

```text
packages/
  server/       FastAPI application, persistence models, migrations and server environment
  web/          React + Vite client and frontend environment
  worker/       RabbitMQ snapshot compiler/importer, game data compiler and worker environment
infrastructure/
  local/        Persistent local Compose stack and Caddy routes
  dev/          Live reload overrides for the local stack
scripts/        Local setup and migration entrypoints
docs/           API, ingestion and implementation notes
```

Each package owns its dependency manifest, lockfile where applicable, environment example and build. The worker installs the server package as a local dependency for the shared ingestion and database code. Database tables, including authentication tables, are defined under `packages/server/src/wuwa_story/db/models`; `auth/` contains account workflows and HTTP behavior.

## Start the development stack

```bash
./scripts/dev-up.sh
make dev
```

The setup script creates missing package environment files from their `.env.example` files, starts PostgreSQL and MinIO, and applies migrations. The dev stack routes the Vite frontend through Caddy at [http://localhost:5173](http://localhost:5173), and the API is also directly available at [http://localhost:8000](http://localhost:8000). Swagger is available at both [http://localhost:8000/docs](http://localhost:8000/docs) and [http://localhost:5173/api/docs](http://localhost:5173/api/docs).

Build and start the production-style local containers with:

```bash
make local-build
make local-up
```

Both Compose files use the `wuwa-story` project name and the existing named PostgreSQL and MinIO volumes.

## Package commands

```bash
cd packages/server && uv sync && uv run uvicorn wuwa_story.api.app:app --reload
cd packages/server && uv run alembic upgrade head
cd packages/server && uv run wuwa-story-admin promote user@example.com
cd packages/worker && uv sync && uv run wuwa-story-worker enqueue-snapshot --version 1.0
cd packages/worker && uv run wuwa-story-worker watch-upstream --from-version 1.0 --once
cd packages/web && npm ci && npm run dev
```

The web client includes searchable character, item, location and quest catalogs, entity profiles, source-linked connections and quest transcripts. API requests use `/api`; Caddy routes them to FastAPI, while the dev Vite server uses its service-level proxy target. Start the GitHub snapshot watcher when needed with `docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile worker up --build -d worker`.

Start the RabbitMQ consumer and GitHub branch scheduler with `docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile worker up --build -d worker snapshot-scheduler`. The worker builds each pinned upstream commit and imports it to PostgreSQL. Queue settings and manual replay of failed messages are documented in [packages/worker/README.md](packages/worker/README.md).

Browser authentication uses server-side opaque sessions in HttpOnly cookies, Argon2id password hashes and CSRF protection. Google login and account linking use the server-side OpenID Connect flow described in [docs/google-oauth.md](docs/google-oauth.md). Configure package-local values in `packages/server/.env` before enabling Google OAuth.

See [docs/architecture.md](docs/architecture.md) for package ownership and [docs/ingestion.md](docs/ingestion.md) for snapshot ingestion and [docs/AUTH_IMPLEMENTATION_REPORT.md](docs/AUTH_IMPLEMENTATION_REPORT.md) for authentication routes and behavior.
