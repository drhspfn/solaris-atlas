FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/packages/server
COPY packages/server/pyproject.toml packages/server/uv.lock ./
COPY packages/server/alembic.ini ./
COPY packages/server/migrations ./migrations
COPY packages/server/src ./src
COPY packages/server/scripts ./scripts
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "wuwa_story.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
