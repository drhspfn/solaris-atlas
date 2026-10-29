"""Choose the safe deployment phase for the one online search index migration."""

import asyncio

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from wuwa_story.config.settings import get_settings

ONLINE = "0023_search_body_trgm"
PREVIOUS = "0022_tree_index_schema_version"


def plan(scripts, current_heads, document_exists=False):
    if len(scripts.get_heads()) != 1 or len(current_heads) > 1:
        raise ValueError("Online migration requires a single Alembic history and database head")
    revisions = list(scripts.walk_revisions())
    for position, revision in enumerate(revisions):
        expected = revisions[position + 1].revision if position + 1 < len(revisions) else None
        if revision.down_revision != expected or revision.dependencies:
            raise ValueError("Online migration requires a linear Alembic history")
    ids = [revision.revision for revision in revisions]
    if ONLINE not in ids or PREVIOUS not in ids or ids.index(PREVIOUS) != ids.index(ONLINE) + 1:
        raise ValueError("Online migration or its expected predecessor is missing")
    if current_heads and current_heads[0] not in ids:
        raise ValueError("Database revision is not present in the deployment image")
    if not current_heads and document_exists:
        raise ValueError("Unversioned database contains search.document; refusing to run metadata DDL")
    current = ids.index(current_heads[0]) if current_heads else len(ids)
    target = ids.index(ONLINE)
    state = "before" if current > target + 1 else "ready" if current == target + 1 else "done"
    return f"{state}:{'last' if target == 0 else 'more'}"


async def main():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            heads = await connection.run_sync(lambda db: MigrationContext.configure(db).get_current_heads())
            document_exists = bool(await connection.scalar(text("SELECT to_regclass('search.document') IS NOT NULL")))
        print(plan(scripts, heads, document_exists))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
