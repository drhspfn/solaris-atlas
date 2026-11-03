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

There is no separate generic `libs/` or global `domain/` layer. Feature-specific
domain models belong with the use cases that need them.

### Notice browsing module

`notices/domain.py` owns persistence-free notice read models and the historical
revision policy: revision content overrides title, category, observation time and
event links while the public identity stays stable. Missing historical metadata
retains the existing current-value fallback; explicitly empty event links stay
empty. Historical artwork never falls back to current artwork.

`notices/service.py` provides list and detail use cases through the
`NoticeRepository` protocol in `notices/repository.py`. The SQLAlchemy adapter is
in `db/repositories/notices.py`, following the shared persistence layout. It owns
source queries, English-only listing, literal title search, ordering and paging.
The service owns the one-row lookahead and public response assembly.

`notices/composition.py` wires the adapter and public image URL resolver. HTTP
routes receive the service through `api/dependencies.py` and FastAPI `Depends`;
they retain query validation, projection routing and domain-to-HTTP error mapping.
Background projection publication calls the same service directly, without
invoking HTTP handlers. Projection reads retain their existing ready/coverage
checks. Ingestion still owns feed parsing and writes; database mappings
and transaction ownership are unchanged.

### Event browsing module

`events/domain.py` contains persistence-free event, occurrence and related read
models. Its policies select capture-scoped client metadata, localized notice
overrides, eligible artwork, preview provenance and verified official links.
`PREVIEW_ROLES` is shared by the SQL summary projection and projection reader.

`events/service.py` implements calendar and run-detail use cases against the
`EventRepository` protocol in `events/repository.py`. It receives media and notice
URL policies through its constructor and imports neither SQLAlchemy, application
settings nor storage clients. Localized presentation is computed once per run.

`db/repositories/event_browsing.py` retains the optimized source queries and
adapts ORM rows and SQL summary rows to domain read models through
`SqlAlchemyEventRepository`. It preserves server/occurrence filters, undated
fallbacks, bounded summary JSON and capture-aware media caches. The adapter owns
the media bucket; the service does not select storage configuration.

`events/composition.py` assembles the repository, service and URL policies.
`api/dependencies.py` provides the service with FastAPI `Depends`; HTTP routes
retain validation, projection routing and domain-to-HTTP 404 mapping. Background
publication uses the composition entry point directly. S3 URL construction does
not initialize an SDK client or perform network calls.

`notices/matching.py` holds the pure authored-identity matching used by both event
browsing and notice ingestion. Ingestion retains compatibility exports for
existing callers and owns all writes and transaction boundaries. No schema,
public response fields or projection publication formats change.

## Environment and build commands

Each package has an ignored `.env` copied from its committed `.env.example`. Local orchestration settings live under `infrastructure/local`. `./scripts/dev-up.sh` creates missing files and applies database migrations.

- Server: `cd packages/server && uv sync`; build with `uv build` or `docker compose ... build api`.
- Worker: `cd packages/worker && uv sync`; build with `uv build` or `docker compose ... build worker`.
- Web: `cd packages/web && npm ci && npm run build`; Docker builds its `runtime` target by default and its `dev` target in the dev override.

Caddy serves the frontend on port 5173, forwards `/api/*` to FastAPI, and also routes Swagger UI paths so `/api/docs` works. FastAPI remains directly available on port 8000 for development and API inspection.
