from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.ops import GameRelease, ImportRun


async def register_release(
    session: AsyncSession,
    *,
    game_version: str,
    resource_version: str | None,
    upstream_name: str,
    upstream_commit: str | None,
    metadata: dict[str, Any] | None = None,
) -> GameRelease:
    # Serialize sequence allocation so parallel import commands cannot claim the same sequence.
    await session.execute(text("SELECT pg_advisory_xact_lock(827411, 1)"))
    identity = select(GameRelease).where(
        GameRelease.game_version == game_version,
        GameRelease.resource_version.is_not_distinct_from(resource_version),
        GameRelease.upstream_name == upstream_name,
        GameRelease.upstream_commit.is_not_distinct_from(upstream_commit),
    )
    release = await session.scalar(identity)
    if release is not None:
        return release
    sequence = (
        int(await session.scalar(select(func.coalesce(func.max(GameRelease.sequence), 0))) or 0) + 1
    )
    release = GameRelease(
        sequence=sequence,
        game_version=game_version,
        resource_version=resource_version,
        upstream_name=upstream_name,
        upstream_commit=upstream_commit,
        metadata_json=metadata or {},
    )
    session.add(release)
    await session.flush()
    return release


async def start_import_run(
    session: AsyncSession,
    release_id: int,
    importer: str,
    importer_version: str,
    metadata: dict[str, Any] | None = None,
) -> ImportRun:
    run = ImportRun(
        release_id=release_id,
        importer=importer,
        importer_version=importer_version,
        metadata_json=metadata or {},
    )
    session.add(run)
    await session.flush()
    return run
