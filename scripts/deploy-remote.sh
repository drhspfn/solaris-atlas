#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${SOLARIS_APP_DIR:-$HOME/solaris-atlas}"
ENV_DIR="$APP_DIR/env"
COMPOSE_FILE="$APP_DIR/compose.yml"
IMAGE_TAG="${IMAGE_TAG:?Set IMAGE_TAG}"
GHCR_OWNER="${GHCR_OWNER:?Set GHCR_OWNER}"
DEPLOY_COMPONENT="${DEPLOY_COMPONENT:?Set DEPLOY_COMPONENT}"

if [[ ! "$IMAGE_TAG" =~ ^[A-Za-z0-9_.-]{1,128}$ ]]; then
  echo "Invalid IMAGE_TAG" >&2
  exit 2
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Production files are missing. Run the infrastructure workflow first." >&2
  exit 1
fi

umask 077
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"
for file in compose.env shared.env api.env worker.env; do
  if [[ ! -f "$ENV_DIR/$file" ]]; then
    echo "Missing generated deployment config: $ENV_DIR/$file" >&2
    echo "Run the GitHub Actions deploy workflow so it can assemble env files from production Variables and Secrets." >&2
    exit 1
  fi
  chmod 600 "$ENV_DIR/$file"
done
USE_MINIO="$(sed -n 's/^USE_MINIO=//p' "$ENV_DIR/compose.env")"
if [[ "$USE_MINIO" != true && "$USE_MINIO" != false ]]; then
  echo "USE_MINIO must be true or false in the GitHub production Variables." >&2
  exit 2
fi

GHCR_TOKEN=""
IFS= read -r GHCR_TOKEN || true
if [[ -n "$GHCR_TOKEN" ]]; then
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io --username "$GHCR_OWNER" --password-stdin >/dev/null
  trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
fi

APP_DIR="$APP_DIR" ENV_DIR="$ENV_DIR" GHCR_OWNER="$GHCR_OWNER" IMAGE_TAG="$IMAGE_TAG" DEPLOY_COMPONENT="$DEPLOY_COMPONENT" python3 <<'PY'
import os
import re
from pathlib import Path
from urllib.parse import quote

env_dir = Path(os.environ["ENV_DIR"])
key_pattern = re.compile(r"[A-Z][A-Z0-9_]*")

def read_env(name):
    result = {}
    path = env_dir / name
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key_pattern.fullmatch(key) or any(c in value for c in "\r\n\0"):
            raise SystemExit(f"Invalid generated environment entry at {path}:{line_number}")
        result[key] = value
    return result

compose = read_env("compose.env")
shared = read_env("shared.env")
api = read_env("api.env")
worker = read_env("worker.env")
use_minio = compose.get("USE_MINIO", "true").lower() == "true"
required = ["DOMAIN", "POSTGRES_USER", "POSTGRES_PASSWORD", "RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS"]
if use_minio:
    required.extend(("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"))
missing = [key for key in required if not compose.get(key)]
if missing:
    raise SystemExit("Missing required production environment values: " + ", ".join(missing))
domain = compose["DOMAIN"].strip()
if not re.fullmatch(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", domain):
    raise SystemExit("DOMAIN must be a hostname without scheme or path")
if os.environ["DEPLOY_COMPONENT"] != "infrastructure" and domain == "example.invalid":
    raise SystemExit("Set the production DOMAIN variable in the GitHub production environment before deploying app services")
if not use_minio:
    required_s3 = ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    missing_s3 = [key for key in required_s3 if not compose.get(key)]
    if missing_s3:
        raise SystemExit("R2 mode requires: " + ", ".join(missing_s3))

runtime = {
    "APP_ENV": "production",
    "DATABASE_URL": f"postgresql+asyncpg://{quote(compose['POSTGRES_USER'], safe='')}:{quote(compose['POSTGRES_PASSWORD'], safe='')}@postgres:5432/wuwa_story",
    "REDIS_URL": "redis://redis:6379/0",
    "RABBITMQ_URL": f"amqp://{quote(compose['RABBITMQ_DEFAULT_USER'], safe='')}:{quote(compose['RABBITMQ_DEFAULT_PASS'], safe='')}@rabbitmq:5672/",
    "S3_ENDPOINT_URL": compose.get("S3_ENDPOINT_URL") or ("http://minio:9000" if use_minio else ""),
    "S3_ACCESS_KEY_ID": compose.get("S3_ACCESS_KEY_ID") or compose.get("MINIO_ROOT_USER", ""),
    "S3_SECRET_ACCESS_KEY": compose.get("S3_SECRET_ACCESS_KEY") or compose.get("MINIO_ROOT_PASSWORD", ""),
    "S3_BUCKET": compose.get("S3_BUCKET", "wuwa"),
    "S3_REGION": compose.get("S3_REGION", "us-east-1"),
    "S3_USE_SSL": compose.get("S3_USE_SSL", "false"),
}
api_url = f"https://api.{domain}"
frontend_url = f"https://{domain}"
derived_api_config = {
    "API_URL": api_url,
    "FRONTEND_URL": frontend_url,
    "MEDIA_PUBLIC_BASE_URL": f"https://cdn.{domain}",
    "CORS_ALLOWED_ORIGINS": frontend_url,
    "AUTH_COOKIE_SECURE": "true",
    "AUTH_COOKIE_DOMAIN": f".{domain}",
    "GOOGLE_REDIRECT_URI": f"{api_url}/auth/google/callback",
}
api_runtime = {**runtime, **api, **derived_api_config}
if "AUTH_CSRF_SECRET" in api and "AUTH_CSRF_SECRET" not in shared:
    shared["AUTH_CSRF_SECRET"] = api["AUTH_CSRF_SECRET"]
if "AUTH_CSRF_SECRET" in shared:
    runtime["AUTH_CSRF_SECRET"] = shared["AUTH_CSRF_SECRET"]

def write_env(name, values):
    for key, value in values.items():
        if not key_pattern.fullmatch(key) or any(c in str(value) for c in "\r\n\0"):
            raise SystemExit(f"Unsafe generated environment value: {key}")
    path = env_dir / name
    path.write_text("".join(f"{key}={value}\n" for key, value in sorted(values.items())), encoding="utf-8")
    os.chmod(path, 0o600)

write_env("runtime.env", runtime)
write_env("api.runtime.env", api_runtime)
write_env("worker.runtime.env", runtime)
write_env("shared.env", shared)
write_env("api.env", {key: value for key, value in api.items() if key not in derived_api_config})
write_env("worker.env", worker)

# Dotenv quoting for Compose interpolation; dollar signs are escaped for Compose.
compose_values = {**compose, "GHCR_OWNER": os.environ["GHCR_OWNER"], "IMAGE_TAG": os.environ["IMAGE_TAG"]}
lines = []
for key, value in sorted(compose_values.items()):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    lines.append(f'{key}="{escaped}"')
path = env_dir / "compose.runtime.env"
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
os.chmod(path, 0o600)
PY

cd "$APP_DIR"
compose=(docker compose --env-file "$ENV_DIR/compose.runtime.env" -f "$COMPOSE_FILE")
if [[ "$USE_MINIO" == true ]]; then
  compose+=(--profile local-storage)
else
  docker compose --profile local-storage -f "$COMPOSE_FILE" --env-file "$ENV_DIR/compose.runtime.env" stop minio minio-init >/dev/null 2>&1 || true
  docker compose --profile local-storage -f "$COMPOSE_FILE" --env-file "$ENV_DIR/compose.runtime.env" rm -f minio minio-init >/dev/null 2>&1 || true
fi
case "$DEPLOY_COMPONENT" in
  infrastructure)
    services=(postgres rabbitmq redis)
    if [[ "$USE_MINIO" == true ]]; then services+=(minio minio-init); fi
    ;;
  api) services=(api) ;;
  web) services=(web) ;;
  caddy) services=(caddy) ;;
  snapshot-worker|story-agent|cutscene-vision) services=("$DEPLOY_COMPONENT") ;;
  all) services=(api web caddy snapshot-worker story-agent cutscene-vision) ;;
  *) echo "Unsupported DEPLOY_COMPONENT: $DEPLOY_COMPONENT" >&2; exit 2 ;;
esac

if [[ "$DEPLOY_COMPONENT" == api || "$DEPLOY_COMPONENT" == all ]]; then
  "${compose[@]}" pull api
  "${compose[@]}" run --rm api alembic upgrade head
fi
if [[ "$DEPLOY_COMPONENT" != infrastructure ]]; then
  "${compose[@]}" pull "${services[@]}"
fi
if [[ "$DEPLOY_COMPONENT" == caddy ]]; then
  "${compose[@]}" up -d --force-recreate caddy
else
  "${compose[@]}" up -d "${services[@]}"
fi
"${compose[@]}" ps
