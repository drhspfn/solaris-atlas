#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/packages/server"
unset WUWA_DB_TRANSACTION_POOLING
if [[ -n "${MIGRATION_DATABASE_URL:-}" ]]; then
  DATABASE_URL="$MIGRATION_DATABASE_URL" uv run alembic upgrade head
else
  uv run alembic upgrade head
fi
uv run python scripts/setup_agent_reader.py
