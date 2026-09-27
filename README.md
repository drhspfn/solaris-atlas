# WuWa Story Platform

Python 3.12 foundation for a source-traceable Wuthering Waves story platform. It provides a PostgreSQL 17 schema, immutable compiler-source ingestion, canonical graph storage, localization, a file-storage abstraction, and a small FastAPI surface. The current scope is infrastructure and deterministic source ingestion; semantic extraction and production search indexing are intentionally separate future work.

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

## Import a compiler dataset

The importer consumes the deterministic filesystem output from the WuWa narrative compiler, including `manifest.json`, `coverage.json`, `raw-evidence/`, `entities/`, and `graphs/global.jsonl`. It verifies indexed raw file hashes and byte sizes, retains raw records and edge provenance, imports all locale values including explicit empty strings, and never generates semantic claims.

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
