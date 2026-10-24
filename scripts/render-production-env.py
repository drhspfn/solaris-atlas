#!/usr/bin/env python3
"""Render production env files from individual GitHub Environment variables/secrets."""

import os
import re
import sys
from decimal import Decimal, InvalidOperation
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
        "WUWA_SNAPSHOT_REPLICAS": "1",
        "WUWA_SNAPSHOT_CPUS": "1.0",
        "WUWA_SNAPSHOT_MEMORY": "2g",
        "WUWA_ASSET_CPUS": "0.5",
        "WUWA_ASSET_MEMORY": "1536m",
        "WUWA_COORDINATOR_REPLICAS": "1",
        "WUWA_COORDINATOR_CPUS": "0.25",
        "WUWA_COORDINATOR_MEMORY": "768m",
        "WUWA_IMAGE_REPLICAS": "1",
        "WUWA_IMAGE_CPUS": "0.75",
        "WUWA_IMAGE_MEMORY": "2g",
        "WUWA_AUDIO_REPLICAS": "1",
        "WUWA_AUDIO_CPUS": "0.75",
        "WUWA_AUDIO_MEMORY": "2g",
        "WUWA_VIDEO_REPLICAS": "1",
        "WUWA_VIDEO_CPUS": "2",
        "WUWA_VIDEO_MEMORY": "2560m",
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
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "TELEGRAM_ERROR_THREAD_ID": "",
        "WUWA_QUEUE_CONCURRENCY": "snapshot_build=1,pool_coordinator=3,pool_image=1,pool_audio=1,pool_video=1",
        "WUWA_SHUTDOWN_TIMEOUT_SECONDS": "90",
        "WUWA_WORKER_DB_POOL_SIZE": "4",
        "WUWA_WORKER_DB_MAX_OVERFLOW": "4",
        "WUWA_WORKSPACE_RECOVERY_SECONDS": "300",
        "WUWA_WORKER_WORKSPACE": "/var/lib/wuwa-worker",
        "WUWA_IMPORT_BATCH_SIZE": "500",
        "WUWA_ASSET_WORKSPACE": "/var/lib/wuwa-worker/client-assets",
        "WUWA_ASSET_DOWNLOAD_CONCURRENCY": "4",
        "WUWA_CLIENT_UPDATE_INTERVAL_SECONDS": "3600",
        "WUWA_ASSET_EXPORT_FILTERS": "ConfigDB",
        "WUWA_ASSET_BUILD_MAPS": "0",
        "AGENT_VISION_ENABLED": "false",
        "AGENT_VISION_FRAME_INTERVAL": "3",
        "AGENT_VISION_MAX_FRAMES": "600",
        "AGENT_VISION_BATCH_FRAMES": "4",
        "AGENT_VISION_OUTPUT_TOKENS": "4096",
    },
}


def validate_worker_config(rendered):
    """Fail before deployment for invalid limits instead of crash-looping workers."""
    compose, worker = rendered["compose.env"], rendered["worker.env"]
    for key, value in compose.items():
        if key.startswith("WUWA_") and key.endswith(("_REPLICAS", "_CPUS")):
            try:
                positive = Decimal(value).is_finite() and Decimal(value) > 0
                if key.endswith("_REPLICAS"):
                    positive = positive and int(value) > 0
            except (ValueError, InvalidOperation):
                positive = False
            if not positive:
                raise SystemExit(f"{key} must be positive")
        if key.startswith("WUWA_") and key.endswith("_MEMORY") and not re.fullmatch(r"[1-9][0-9]*(?:[bkmgBKMG](?:[bB])?)?", value):
            raise SystemExit(f"{key} must be a positive Compose memory size")
    limits = {}
    try:
        for entry in worker["WUWA_QUEUE_CONCURRENCY"].split(","):
            if not entry.strip():
                continue
            key, value = entry.strip().split("=", 1)
            limits[key.strip()] = int(value)
        if any(value < 1 for value in limits.values()):
            raise ValueError
        if limits.get("snapshot_build", 1) * int(compose["WUWA_SNAPSHOT_REPLICAS"]) > 4:
            raise ValueError
        if float(worker["WUWA_WORKSPACE_RECOVERY_SECONDS"]) <= 0:
            raise ValueError
        if not 0 < float(worker["WUWA_SHUTDOWN_TIMEOUT_SECONDS"]) < 120:
            raise ValueError
        capacity = int(worker["WUWA_WORKER_DB_POOL_SIZE"]) + int(worker["WUWA_WORKER_DB_MAX_OVERFLOW"])
        if int(worker["WUWA_WORKER_DB_POOL_SIZE"]) < 1 or int(worker["WUWA_WORKER_DB_MAX_OVERFLOW"]) < 0:
            raise ValueError
        if any(2 * limits.get(f"pool_{pool}", 3 if pool == "coordinator" else 1) + 2 > capacity
               for pool in ("coordinator", "image", "audio", "video")):
            raise ValueError
    except ValueError as error:
        raise SystemExit("Invalid worker limits: positive concurrency, snapshot replicas * concurrency <=4, shutdown <120s, DB capacity >= 2 * pool concurrency +2 required") from error


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

    validate_worker_config(rendered)

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
