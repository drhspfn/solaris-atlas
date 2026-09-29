# Package and service boundaries

The repository is a small monorepo with three independently buildable applications:

- `packages/server` owns the FastAPI application, account workflows, PostgreSQL mappings, migrations, localization, graph/search services, and the shared deterministic import library.
- `packages/worker` owns the RabbitMQ consumer/scheduler and bundled deterministic snapshot compiler. It has its own `pyproject.toml`, lockfile, virtual environment, source package, and image. It installs `wuwa-story-server` from the sibling package for database and import functionality.
- `packages/web` owns the React/Vite client, npm lockfile, frontend environment, and its development and production image targets.

`infrastructure/local` contains the persistent Compose stack and Caddy routing. `infrastructure/dev` overlays live reload and source mounts on that stack. Both use the Compose project name `wuwa-story`, retaining PostgreSQL, MinIO, RabbitMQ, and worker data volumes.

## Server module boundaries

Within `packages/server/src/wuwa_story`:

- `api` contains FastAPI composition, transport dependencies, and route modules. Story browse routes are grouped into catalog, profiles, and transcripts with shared source-backed payload builders.
- `auth` contains account policy, validation, OAuth, session services, and HTTP routes.
- `db/models` is the single SQLAlchemy mapping registry, including `db/models/auth.py`; `db/repositories` contains reusable persistence queries.
- `ingestion` handles canonical/raw snapshot processing, release registration, and GitHub snapshot operations that are shared by the command worker.
- `graph`, `search`, and `storage` own their focused services and adapters.

There is no separate generic `libs/` or empty `domain/` layer: the current project has no independent, persistence-free domain model that would justify a package boundary there. Introduce one when a real use case needs logic shared across transport or storage implementations.

## Environment and build commands

Each package has an ignored `.env` copied from its committed `.env.example`. Local orchestration settings live under `infrastructure/local`. `./scripts/dev-up.sh` creates missing files and applies database migrations.

- Server: `cd packages/server && uv sync`; build with `uv build` or `docker compose ... build api`.
- Worker: `cd packages/worker && uv sync`; build with `uv build` or `docker compose ... build worker`.
- Web: `cd packages/web && npm ci && npm run build`; Docker builds its `runtime` target by default and its `dev` target in the dev override.

Caddy serves the frontend on port 5173, forwards `/api/*` to FastAPI, and also routes Swagger UI paths so `/api/docs` works. FastAPI remains directly available on port 8000 for development and API inspection.
