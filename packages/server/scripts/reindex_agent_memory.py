"""Explicit, budgeted memory reindex after changing embedding model or dimensions."""

import argparse
import asyncio
import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.agents.workspace import paid_vector
from wuwa_story.db.models.agent_workspace import AgentMemory, AgentMemoryEmbedding
from wuwa_story.db.session import SessionFactory


async def reindex(after: int, limit: int) -> None:
    settings = get_agent_settings()
    if not settings.embedding_model:
        raise ValueError("Configure AGENT_EMBEDDING_MODEL before indexing")
    async with SessionFactory() as session:
        rows = list(
            await session.scalars(
                select(AgentMemory)
                .where(AgentMemory.scope == "long", AgentMemory.id > after)
                .order_by(AgentMemory.id)
                .limit(limit)
            )
        )
        entries = [(row.id, row.revision, row.text) for row in rows]
        await session.commit()
        for identity, revision, content in entries:
            vector = await paid_vector(session, settings, content)
            if vector is None:
                print(
                    f"Stopped at memory {identity}: budget, configuration or provider unavailable"
                )
                return
            current = await session.scalar(
                select(AgentMemory)
                .where(AgentMemory.id == identity)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current is None or current.revision != revision:
                await session.rollback()
                continue
            values = dict(
                memory_id=identity,
                model_id=vector[0],
                embedding=vector[1],
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            )
            await session.execute(
                insert(AgentMemoryEmbedding)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[AgentMemoryEmbedding.memory_id, AgentMemoryEmbedding.model_id],
                    set_=values,
                )
            )
            await session.commit()
            print(f"Indexed memory {identity}; next --after {identity}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=20)
    args = parser.parse_args()
    asyncio.run(reindex(args.after, args.limit))
