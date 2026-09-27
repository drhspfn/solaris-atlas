# Foundation implementation report

## Implemented

- Python 3.12 package and configuration with FastAPI, SQLAlchemy async, Alembic, Pydantic Settings, asyncpg, pgvector, and `uv`.
- Ten-schema relational model covering releases/imports, immutable source evidence, localization, canonical graph/evidence, typed narrative/game entities, ontology, claims/events, documents, search projections, and embeddings.
- 70 SQLAlchemy tables across the ten schemas; the migration also emits successfully in Alembic offline SQL mode.
- Source-traceable deterministic importer for compiler filesystem outputs; raw source files are verified against SHA-256 and size in the source index.
- Content-addressed local/S3-compatible storage abstraction with file metadata, locations, variants, and references.
- Health, release, node/edge, and search API routes; lexical resolver/ranking and persistence contracts for future semantic search.
- PostgreSQL/MinIO/API Compose stack, development container definitions, initial migration, and system documentation.
- Unit tests for content identity, local path safety, compiler dataset/provenance validation, and database-independent API health.
- Read-only adapter validation against the existing 3.6.0 compiled dataset: 41 entity JSONL files / 400,297 entities and 615,328 provenance-bearing graph edges.

## Explicitly not implemented

- Semantic or LLM event/entity extraction, story inference, production vector generation, task queue, or search ranking model.
- Game-file extraction/decryption, upstream repository synchronization, and physical game media ingestion.
- Production authentication/authorization, deployment secrets, backups, observability stack, or migration rollout policy.
- Full importer CLI and end-to-end DB integration validation.

## Validation status

Validated with Python 3.12.13, 7 unit tests (1 optional database test skipped unless `WUWA_TEST_DATABASE_URL` is set), Ruff, Python syntax compilation, `docker compose config`, and Alembic offline SQL generation (1,090 SQL lines). The full Compose stack was subsequently built and started; migration `0001_initial_foundation` was applied to PostgreSQL 17, all 70 domain tables and the `vector`, `pg_trgm`, and `pgcrypto` extensions were verified, and both `/health` and `/health/db` returned `ok`. Strict mypy still reports typing issues in dynamic canonical record normalization and third-party libraries without bundled stubs. The local MinIO CE image is community-built because the old image references were unavailable from their registries at runtime; review/pin an approved image before production use.
