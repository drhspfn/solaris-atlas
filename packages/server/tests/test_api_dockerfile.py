"""Keep expensive runtime dependencies independent of server source changes."""

from pathlib import Path


def test_api_image_caches_dependencies_before_copying_project_sources():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    instructions = [line.strip() for line in dockerfile.splitlines() if line.strip()]
    dependency_copy = instructions.index("COPY packages/server/pyproject.toml packages/server/uv.lock ./")
    dependency_sync = instructions.index(
        "RUN uv sync --frozen --no-dev --extra visual-memory --no-install-project"
    )
    project_sync = instructions.index("RUN uv sync --frozen --no-dev --extra visual-memory")
    project_copies = [instructions.index(line) for line in (
        "COPY packages/server/alembic.ini ./",
        "COPY packages/server/migrations ./migrations",
        "COPY packages/server/src ./src",
        "COPY packages/server/scripts ./scripts",
    )]
    assert dependency_copy < dependency_sync < min(project_copies)
    assert max(project_copies) < project_sync < instructions.index("USER appuser")
    assert "RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app" in instructions
