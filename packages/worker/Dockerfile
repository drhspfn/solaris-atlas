FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/packages/worker
COPY packages/server/pyproject.toml /app/packages/server/pyproject.toml
COPY packages/server/src /app/packages/server/src
COPY packages/worker/pyproject.toml packages/worker/uv.lock ./
COPY packages/worker/src ./src
RUN uv sync --frozen --no-dev
RUN mkdir -p /var/lib/wuwa-sync && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /var/lib/wuwa-sync
USER appuser
CMD ["wuwa-story-worker", "run"]
