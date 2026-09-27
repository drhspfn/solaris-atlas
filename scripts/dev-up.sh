#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example; review the local credentials before exposing services."
fi

uv sync --python 3.12
docker compose up -d postgres minio minio-init
uv run alembic upgrade head
echo "Database and object store are ready. Start the API with: uv run uvicorn wuwa_story.api.app:app --reload"
