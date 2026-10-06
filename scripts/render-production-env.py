#!/usr/bin/env python3
"""Render production env files from individual GitHub Environment variables/secrets."""

import os
import re
import sys
from pathlib import Path

KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*")

GROUPS = {
    "compose.env": {
        "DOMAIN": "example.invalid",
        "USE_MINIO": "true",
        "POSTGRES_USER": "wuwa",
        "POSTGRES_PASSWORD": None,
        "RABBITMQ_DEFAULT_USER": "wuwa",
        "RABBITMQ_DEFAULT_PASS": None,
        "MINIO_ROOT_USER": "solaris-atlas",
        "MINIO_ROOT_PASSWORD": "",
        "S3_ACCESS_KEY_ID": "",
        "S3_SECRET_ACCESS_KEY": "",
        "S3_ENDPOINT_URL": "",
        "S3_BUCKET": "wuwa",
        "S3_REGION": "",
        "S3_USE_SSL": "",
    },
    "shared.env": {
        "AGENT_PROVIDER": "responses",
        "AGENT_MODEL": "gpt-6-luna",
        "AGENT_BASE_URL": "https://api.openai.com/v1",
        "AGENT_API_KEY": "",
        "AGENT_DAILY_BUDGET_USD": "1",
        "AGENT_BUDGET_TIMEZONE": "Europe/Kyiv",
        "AGENT_DAILY_TOKEN_LIMIT": "0",
        "AGENT_MAX_INPUT_TOKENS": "250000",
        "AGENT_MAX_TOOL_CALLS_PER_STEP": "20",
        "AGENT_CONTEXT_COMPACTION": "true",
        "AGENT_COMPACTION_THRESHOLD_RATIO": "0.8",
        "AGENT_COMPACTION_OUTPUT_TOKENS": "32000",
        "AGENT_RATE_LIMIT_RETRIES": "3",
        "AGENT_RATE_LIMIT_BACKOFF_SECONDS": "30",
        "AGENT_RATE_LIMIT_WAIT_SECONDS": "180",
        "AGENT_INPUT_USD_PER_MILLION": "0.10",
        "AGENT_CACHED_INPUT_USD_PER_MILLION": "0.01",
        "AGENT_CACHE_WRITE_USD_PER_MILLION": "0.125",
        "AGENT_OUTPUT_USD_PER_MILLION": "0.50",
        "AGENT_PRICE_SAFETY_MULTIPLIER": "1.25",
        "AGENT_EMBEDDING_MODEL": "",
        "AGENT_EMBEDDING_DIMENSIONS": "1536",
        "AGENT_EMBEDDING_INPUT_USD_PER_MILLION": "0.02",
        "AGENT_PUBLIC_QUERY_EMBEDDINGS": "false",
        "AGENT_QUERY_REQUESTS_PER_HOUR": "30",
        "AUTH_CSRF_SECRET": None,
    },
    "api.env": {
        "APP_NAME": "WuWa Story Platform",
        "MEDIA_CACHE_CONTROL": "public, max-age=31536000, immutable",
        "API_CACHE_NAMESPACE": "solaris-api-v1",
        "API_CACHE_TTL_SECONDS": "3600",
        "API_CACHE_MAX_BODY_BYTES": "8388608",
        "API_CACHE_TIMEOUT_SECONDS": "0.3",
        "LOG_LEVEL": "INFO",
        "AUTH_SESSION_TTL_DAYS": "30",
        "AUTH_PENDING_REGISTRATION_TTL_MINUTES": "15",
        "AUTH_PENDING_LINK_TTL_MINUTES": "10",
        "AUTH_COOKIE_NAME": "solaris_session",
        "AUTH_COOKIE_SECURE": "true",
        "AUTH_COOKIE_SAMESITE": "lax",
        "AUTH_CSRF_COOKIE_NAME": "solaris_csrf",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
    },
    "worker.env": {
        "LOG_LEVEL": "INFO",
        "WUWA_QUEUE_CONCURRENCY": "snapshot_build=1",
        "WUWA_WORKER_WORKSPACE": "/var/lib/wuwa-worker",
        "WUWA_IMPORT_BATCH_SIZE": "500",
        "WUWA_ASSET_WORKSPACE": "/var/lib/wuwa-worker/client-assets",
        "WUWA_ASSET_DOWNLOAD_CONCURRENCY": "4",
        "WUWA_ASSET_EXPORT_FILTERS": "ConfigDB",
        "WUWA_ASSET_BUILD_MAPS": "0",
        "AGENT_VISION_ENABLED": "false",
        "AGENT_VISION_FRAME_INTERVAL": "3",
        "AGENT_VISION_MAX_FRAMES": "600",
        "AGENT_VISION_BATCH_FRAMES": "4",
        "AGENT_VISION_OUTPUT_TOKENS": "4096",
    },
}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: render-production-env.py OUTPUT_DIR")
    output_dir = Path(sys.argv[1])
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)

    missing = []
    rendered = {}
    for filename, settings in GROUPS.items():
        values = {}
        for key, default in settings.items():
            value = os.environ.get(key)
            if value is None or (value == "" and default not in ("", None)):
                value = default
            if value is None or (key in {"POSTGRES_PASSWORD", "RABBITMQ_DEFAULT_PASS", "AUTH_CSRF_SECRET"} and not value):
                missing.append(key)
                continue
            if not KEY_RE.fullmatch(key) or any(char in value for char in "\r\n\0"):
                raise SystemExit(f"Invalid value for {key}: environment values must be single-line")
            if value == "":
                continue
            values[key] = value
        rendered[filename] = values

    if missing:
        raise SystemExit("Set these GitHub production Secrets before deploying: " + ", ".join(sorted(set(missing))))

    use_minio = rendered["compose.env"].get("USE_MINIO", "true").lower()
    if use_minio not in {"true", "false"}:
        raise SystemExit("USE_MINIO must be either true or false")
    if use_minio == "false":
        rendered["compose.env"].setdefault("S3_REGION", "auto")
        rendered["compose.env"].setdefault("S3_USE_SSL", "true")
        required_r2 = {"S3_ENDPOINT_URL": "Variable", "S3_ACCESS_KEY_ID": "Variable", "S3_SECRET_ACCESS_KEY": "Secret"}
        missing_r2 = [f"{key} ({kind})" for key, kind in required_r2.items() if not rendered["compose.env"].get(key)]
        if missing_r2:
            raise SystemExit("Set these GitHub production R2 settings when USE_MINIO=false: " + ", ".join(missing_r2))
    else:
        rendered["compose.env"].setdefault("S3_REGION", "us-east-1")
        rendered["compose.env"].setdefault("S3_USE_SSL", "false")
        if not rendered["compose.env"].get("MINIO_ROOT_PASSWORD"):
            raise SystemExit("Set MINIO_ROOT_PASSWORD as a GitHub production Secret when USE_MINIO=true")

    for filename, values in rendered.items():
        path = output_dir / filename
        path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
        os.chmod(path, 0o600)


if __name__ == "__main__":
    main()
