#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for package in server worker web; do
  if [[ ! -f "$ROOT/packages/$package/.env" ]]; then
    cp "$ROOT/packages/$package/.env.example" "$ROOT/packages/$package/.env"
    echo "Created packages/$package/.env from its example."
  fi
done
if [[ ! -f "$ROOT/infrastructure/local/.env" ]]; then
  cp "$ROOT/infrastructure/local/.env.example" "$ROOT/infrastructure/local/.env"
  echo "Created infrastructure/local/.env from its example."
fi

cd "$ROOT"
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env up -d postgres minio minio-init
(
  cd packages/server
  uv run alembic upgrade head
)
echo "Local data services are ready. Start the development stack with: make dev"
